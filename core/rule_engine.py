"""
Rule Engine Module
Monitors user feedback patterns, automatically detects and persists repeated patterns as business rules
Supports keyword routing, intent rules, retrieval parameter rules, etc.
"""
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import config
from service.logger import get_logger
from service.i18n import _

logger = get_logger('rule_engine')


class BusinessRule:
    """Business rule"""

    def __init__(self, rule_id: str, rule_type: str, pattern: Dict, action: Dict, description: str = ""):
        self.rule_id = rule_id
        self.rule_type = rule_type
        self.pattern = pattern
        self.action = action
        self.description = description
        self.created_at = None
        self.usage_count = 0
        self.last_used_at = None
        self.effective_count = 0
        self.ineffective_count = 0
        self.avg_evaluation_score = 0.0
        self.evaluation_scores: List[float] = []

    def record_effectiveness(self, effective: bool, evaluation_score: float = None):
        """Record rule application effectiveness"""
        if effective:
            self.effective_count += 1
        else:
            self.ineffective_count += 1
        
        if evaluation_score is not None:
            self.evaluation_scores.append(evaluation_score)
            self.avg_evaluation_score = sum(self.evaluation_scores) / len(self.evaluation_scores)

    def get_effectiveness_rate(self) -> float:
        """Get rule effectiveness rate"""
        total = self.effective_count + self.ineffective_count
        return self.effective_count / total if total > 0 else 0.0

    def to_dict(self) -> Dict:
        return {
            'rule_id': self.rule_id,
            'rule_type': self.rule_type,
            'pattern': self.pattern,
            'action': self.action,
            'description': self.description,
            'created_at': self.created_at,
            'usage_count': self.usage_count,
            'last_used_at': self.last_used_at,
            'effective_count': self.effective_count,
            'ineffective_count': self.ineffective_count,
            'effectiveness_rate': self.get_effectiveness_rate(),
            'avg_evaluation_score': round(self.avg_evaluation_score, 4),
            'evaluation_count': len(self.evaluation_scores)
        }


class RuleEngine:
    """Rule engine"""

    def __init__(self):
        self.rule_file = Path(config.get('rule_engine.rule_file', './storage/rules.json'))
        self.feedback_file = Path(config.get('rule_engine.feedback_file', './storage/feedbacks.json'))
        self.rules: Dict[str, BusinessRule] = {}
        self.min_feedback_count = config.get('rule_engine.min_feedback_count', 3)
        self.feedback_history: List[Dict] = []
        self.rule_application_logs: List[Dict] = []
        self._max_logs = config.get('rule_engine.max_logs', 1000)
        self._load_rules()
        self._load_feedbacks()

        # ---- Memory buffer + timed batch persistence ----
        # Avoid synchronous disk writes on every user feedback, which generates heavy disk I/O under high concurrency.
        # Background daemon thread flushes rules / feedbacks to JSON files every _flush_interval seconds,
        # or when accumulated changes reach _flush_threshold entries.
        self._rules_dirty = False
        self._feedbacks_dirty = False
        self._pending_changes = 0
        self._flush_interval = config.get('rule_engine.flush_interval', 10)  # seconds
        self._flush_threshold = config.get('rule_engine.flush_threshold', 50)  # entries
        self._flush_lock = threading.Lock()
        self._flush_stop = threading.Event()
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True, name='rule_engine_flush')
        self._flush_thread.start()
        logger.info(f"Rule engine buffer persistence started (interval {self._flush_interval}s / threshold {self._flush_threshold} entries)")



    def _load_rules(self):
        """Load rule file"""
        if self.rule_file.exists():
            try:
                with open(self.rule_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for rule_data in data:
                        rule = BusinessRule(
                            rule_id=rule_data['rule_id'],
                            rule_type=rule_data['rule_type'],
                            pattern=rule_data['pattern'],
                            action=rule_data['action'],
                            description=rule_data.get('description', '')
                        )
                        rule.created_at = rule_data.get('created_at')
                        rule.usage_count = rule_data.get('usage_count', 0)
                        rule.last_used_at = rule_data.get('last_used_at')
                        rule.effective_count = rule_data.get('effective_count', 0)
                        rule.ineffective_count = rule_data.get('ineffective_count', 0)
                        rule.avg_evaluation_score = rule_data.get('avg_evaluation_score', 0.0)
                        rule.evaluation_scores = rule_data.get('evaluation_scores', [])
                        self.rules[rule.rule_id] = rule
                logger.info(f"Loaded {len(self.rules)} rules")
            except Exception as e:
                logger.error(f"Loading rule file failed: {e}")

    def _save_rules(self):
        """Save rule file"""
        try:
            data = [rule.to_dict() for rule in self.rules.values()]
            with open(self.rule_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Saving rule file failed: {e}")

    def _load_feedbacks(self):
        """Load feedback history file"""
        if self.feedback_file.exists():
            try:
                with open(self.feedback_file, 'r', encoding='utf-8') as f:
                    self.feedback_history = json.load(f)
                logger.info(f"Loaded {len(self.feedback_history)} feedback records")
            except Exception as e:
                logger.error(f"Loading feedback file failed: {e}")
                self.feedback_history = []

    def _save_feedbacks(self):
        """Save feedback history file (actually write to disk)"""
        try:
            with open(self.feedback_file, 'w', encoding='utf-8') as f:
                json.dump(self.feedback_history, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved {len(self.feedback_history)} feedback records")
        except Exception as e:
            logger.error(f"Saving feedback file failed: {e}")

    # ============ Memory buffer and batch persistence ============

    def _mark_rules_dirty(self):
        """Mark rules as changed, wait for batch flush (replaces synchronous _save_rules each time)"""
        with self._flush_lock:
            self._rules_dirty = True
            self._pending_changes += 1
            if self._pending_changes >= self._flush_threshold:
                # Reach threshold, trigger flush immediately
                threading.Thread(target=self._flush, daemon=True).start()

    def _mark_feedbacks_dirty(self):
        """Mark feedbacks as changed, wait for batch flush (replaces synchronous _save_feedbacks each time)"""
        with self._flush_lock:
            self._feedbacks_dirty = True
            self._pending_changes += 1
            if self._pending_changes >= self._flush_threshold:
                # Reach threshold, trigger flush immediately
                threading.Thread(target=self._flush, daemon=True).start()

    def _flush_loop(self):
        """Background daemon thread: timed flush"""
        while not self._flush_stop.wait(timeout=self._flush_interval):
            try:
                self._flush()
            except Exception as e:
                logger.error(f"Timed flush failed: {e}")

    def _flush(self):
        """Batch write dirty-marked data in memory to disk"""
        with self._flush_lock:
            rules_dirty = self._rules_dirty
            feedbacks_dirty = self._feedbacks_dirty
            self._rules_dirty = False
            self._feedbacks_dirty = False
            self._pending_changes = 0

        if rules_dirty:
            self._save_rules()
        if feedbacks_dirty:
            self._save_feedbacks()

    def flush(self):
        """Manually trigger flush (for external calls, e.g. before service shutdown)"""
        self._flush()

    def stop(self):
        """Stop background flush thread and perform final flush"""
        self._flush_stop.set()
        self._flush()
        logger.info(" Rule engine buffer persistence stopped")

    def add_rule(self, rule: BusinessRule):
        """Add rule"""
        self.rules[rule.rule_id] = rule
        self._mark_rules_dirty()
        logger.info(f" Added rule: {rule.rule_id} ({rule.description})")

    def remove_rule(self, rule_id: str):
        """Delete rule"""
        if rule_id in self.rules:
            del self.rules[rule_id]
            self._mark_rules_dirty()
            logger.info(f" Removed rule: {rule_id}")

    def get_rules_by_type(self, rule_type: str) -> List[BusinessRule]:
        """Get rules by type"""
        return [rule for rule in self.rules.values() if rule.rule_type == rule_type]

    def evaluate_query(self, query: str, kb_id: str = None) -> List[Dict]:
        """
        Evaluate query, return matched rules and their actions

        Args:
            query: User query
            kb_id: Knowledge base ID (optional)

        Returns:
            Matched rule action list
        """
        logger.info(f"[Rule engine] Start evaluate query: {query[:50]}..., kb: {kb_id or 'None'}, total rules: {len(self.rules)}")
        matched_actions = []

        for rule in self.rules.values():
            matched = self._match_rule(rule, query, kb_id)
            if matched:
                logger.info(f"[Rule engine] Rule matched, rule ID: {rule.rule_id}, type: {rule.rule_type}, description: {rule.description}")
                matched_actions.append({
                    'rule_id': rule.rule_id,
                    'rule_type': rule.rule_type,
                    'description': rule.description,
                    'action': rule.action
                })
                rule.usage_count += 1
                rule.last_used_at = str(datetime.now())
            else:
                logger.debug(f"[Rule engine] Rule not matched, rule ID: {rule.rule_id}, type: {rule.rule_type}")

        self._mark_rules_dirty()
        logger.info(f"[Rule engine] Query evaluation completed, matched rules: {len(matched_actions)}")
        return matched_actions

    def _match_rule(self, rule: BusinessRule, query: str, kb_id: str = None) -> bool:
        """Check whether query matches rule"""
        pattern = rule.pattern

        if 'keywords' in pattern:
            keywords = pattern['keywords']
            match_type = pattern.get('match_type', 'any')

            if match_type == 'all':
                if not all(kw in query for kw in keywords):
                    return False
            else:
                if not any(kw in query for kw in keywords):
                    return False

        if 'intent_types' in pattern:
            from core.intent_classifier import intent_classifier
            intent_info = intent_classifier.classify(query)
            if intent_info['intent_type'] not in pattern['intent_types']:
                return False

        if 'kb_ids' in pattern and kb_id:
            if kb_id not in pattern['kb_ids']:
                return False

        return True

    def record_feedback(self, feedback: Dict) -> List[Dict]:
        """
        Record user feedback

        Args:
            feedback: Feedback dictionary, contains query, intent_type, correction, kb_id, feedback_type, etc.

        Returns:
            Rule suggestion list (if threshold reached)
        """
        feedback['timestamp'] = feedback.get('timestamp', str(datetime.now()))
        
        feedback_type = feedback.get('feedback_type', 'other')
        feedback['feedback_type'] = feedback_type
        
        self.feedback_history.append(feedback)
        self._mark_feedbacks_dirty()

        logger.info(f"[Rule engine] Record user feedback, type: {feedback_type}, trace_id: {feedback.get('trace_id', '')}")

        if len(self.feedback_history) >= self.min_feedback_count:
            suggestions = self._detect_patterns()
            if suggestions:
                logger.info(f"[Rule engine] Detected {len(suggestions)} rule suggestions")
            return suggestions
        
        return []

    def _detect_patterns(self):
        """Detect repeated feedback patterns, generate rule suggestions"""
        pattern_counts = {}

        for feedback in self.feedback_history:
            feedback_type = feedback.get('feedback_type', 'other')
            intent_type = feedback.get('intent_type', '')
            correction_type = feedback.get('correction_type', '')
            
            if feedback_type == 'intent_error':
                key = ('intent_error', intent_type)
            elif feedback_type == 'recall_missing':
                key = ('recall_missing', feedback.get('kb_id', ''))
            elif feedback_type == 'answer_wrong':
                key = ('answer_wrong', intent_type)
            elif feedback_type == 'citation_issue':
                key = ('citation_issue', intent_type)
            else:
                key = ('other', intent_type)
            
            if key not in pattern_counts:
                pattern_counts[key] = {
                    'count': 0,
                    'examples': [],
                    'suggested_action': feedback.get('suggested_action'),
                    'feedback_type': feedback_type
                }
            pattern_counts[key]['count'] += 1
            pattern_counts[key]['examples'].append(feedback.get('query')[:50])

        suggestions = []
        matched_keys = set()
        for key, data in pattern_counts.items():
            if data['count'] >= self.min_feedback_count:
                matched_keys.add(key)
                feedback_type, sub_key = key
                
                if feedback_type == 'intent_error':
                    rule_type = 'intent_correction'
                    description = f"Repeated intent recognition error: {sub_key}"
                    pattern = {'intent_types': [sub_key], 'match_type': 'any'}
                elif feedback_type == 'recall_missing':
                    rule_type = 'recall_boost'
                    description = f"Repeated missing recall: Knowledge base {sub_key}"
                    pattern = {'kb_ids': [sub_key], 'match_type': 'any'}
                elif feedback_type == 'answer_wrong':
                    rule_type = 'answer_validation'
                    description = f"Repeated answer error: {sub_key}"
                    pattern = {'intent_types': [sub_key], 'match_type': 'any'}
                elif feedback_type == 'citation_issue':
                    rule_type = 'citation_enforcement'
                    description = f"Repeated citation issue: {sub_key}"
                    pattern = {'intent_types': [sub_key], 'match_type': 'any'}
                else:
                    rule_type = 'custom'
                    description = f"Repeated feedback pattern: {sub_key}"
                    pattern = {'intent_types': [sub_key], 'match_type': 'any'}
                
                suggestions.append({
                    'rule_type': rule_type,
                    'description': description,
                    'pattern': pattern,
                    'action': data['suggested_action'] or {},
                    'count': data['count'],
                    'examples': data['examples'][:5],
                    'feedback_type': feedback_type
                })

        if matched_keys:
            remaining_feedbacks = []
            for feedback in self.feedback_history:
                feedback_type = feedback.get('feedback_type', 'other')
                intent_type = feedback.get('intent_type', '')
                
                if feedback_type == 'intent_error':
                    key = ('intent_error', intent_type)
                elif feedback_type == 'recall_missing':
                    key = ('recall_missing', feedback.get('kb_id', ''))
                elif feedback_type == 'answer_wrong':
                    key = ('answer_wrong', intent_type)
                elif feedback_type == 'citation_issue':
                    key = ('citation_issue', intent_type)
                else:
                    key = ('other', intent_type)
                
                if key not in matched_keys:
                    remaining_feedbacks.append(feedback)
            
            removed_count = len(self.feedback_history) - len(remaining_feedbacks)
            self.feedback_history = remaining_feedbacks
            self._mark_feedbacks_dirty()
            logger.info(f"[Rule engine] Removed {removed_count} matched feedback records, remaining {len(self.feedback_history)} records")
        
        return suggestions

    def suggest_rules(self) -> List[Dict]:
        """Get rule suggestions"""
        return self._detect_patterns()

    def create_rule_from_suggestion(self, suggestion: Dict) -> BusinessRule:
        """Create rule from suggestion"""
        import uuid
        rule = BusinessRule(
            rule_id=str(uuid.uuid4()),
            rule_type=suggestion['rule_type'],
            pattern=suggestion['pattern'],
            action=suggestion['action'],
            description=suggestion['description']
        )
        rule.created_at = str(datetime.now())
        self.add_rule(rule)
        return rule

    def list_rules(self) -> List[Dict]:
        """List all rules"""
        return [rule.to_dict() for rule in self.rules.values()]

    def get_rule(self, rule_id: str) -> Optional[BusinessRule]:
        """Get rule"""
        return self.rules.get(rule_id)

    def log_rule_application(self, rule_id: str, query: str, action_taken: Dict, result_summary: Dict):
        """
        Log rule application
        
        Args:
            rule_id: Rule ID
            query: User query
            action_taken: Executed action
            result_summary: Result summary (contains retrieval result count, evaluation score, etc.)
        """
        log_entry = {
            'rule_id': rule_id,
            'query': query,
            'action_taken': action_taken,
            'result_summary': result_summary,
            'timestamp': str(datetime.now()),
            'trace_id': result_summary.get('trace_id', '')
        }
        
        self.rule_application_logs.append(log_entry)
        
        if len(self.rule_application_logs) > self._max_logs:
            self.rule_application_logs = self.rule_application_logs[-self._max_logs:]
        
        logger.info(f"[Rule engine] Logged rule application log, rule ID: {rule_id}, query: {query[:50]}..., action: {action_taken}, result summary: {result_summary}, trace_id: {result_summary.get('trace_id', '')}")

    def evaluate_rule_effectiveness(self, rule_id: str, feedback: Dict):
        """
        Evaluate rule application effectiveness
        
        Args:
            rule_id: Rule ID
            feedback: Feedback info (contains score, whether effective, etc.)
        """
        rule = self.rules.get(rule_id)
        if not rule:
            logger.warning(f"[Rule engine] Rule not found, rule ID: {rule_id}")
            return
        
        effective = feedback.get('effective', False)
        evaluation_score = feedback.get('evaluation_score')
        
        logger.info(f"[Rule engine] Rule effectiveness evaluation, rule ID: {rule_id}, effective: {effective}, score: {evaluation_score}")
        
        rule.record_effectiveness(effective, evaluation_score)
        self._mark_rules_dirty()
        
        effectiveness_rate = rule.get_effectiveness_rate()
        logger.info(f"[Rule engine] Rule effectiveness evaluation completed, rule ID: {rule_id}, effective: {rule.effective_count}, ineffective: {rule.ineffective_count}, efficiency: {effectiveness_rate:.2%}, avg score: {rule.avg_evaluation_score:.3f}")

    def get_rule_effectiveness_report(self, rule_id: str = None) -> Dict:
        """
        Get rule effectiveness rate report
        
        Args:
            rule_id: Rule ID (optional, returns all rules if not passed)
        
        Returns:
            Effectiveness rate report
        """
        if rule_id:
            rule = self.rules.get(rule_id)
            if not rule:
                return {'error': _('rule.not_found')}
            
            recent_logs = [
                log for log in self.rule_application_logs 
                if log['rule_id'] == rule_id
            ][-20:]
            
            return {
                'rule_id': rule_id,
                'rule_type': rule.rule_type,
                'description': rule.description,
                'usage_count': rule.usage_count,
                'effective_count': rule.effective_count,
                'ineffective_count': rule.ineffective_count,
                'effectiveness_rate': rule.get_effectiveness_rate(),
                'avg_evaluation_score': rule.avg_evaluation_score,
                'recent_applications': recent_logs,
                'status': self._get_rule_status(rule)
            }
        
        reports = []
        for rule in self.rules.values():
            reports.append({
                'rule_id': rule.rule_id,
                'rule_type': rule.rule_type,
                'description': rule.description,
                'usage_count': rule.usage_count,
                'effective_count': rule.effective_count,
                'ineffective_count': rule.ineffective_count,
                'effectiveness_rate': rule.get_effectiveness_rate(),
                'avg_evaluation_score': rule.avg_evaluation_score,
                'status': self._get_rule_status(rule)
            })
        
        return {
            'total_rules': len(self.rules),
            'total_applications': sum(r.usage_count for r in self.rules.values()),
            'avg_effectiveness_rate': sum(r.get_effectiveness_rate() for r in self.rules.values()) / len(self.rules) if self.rules else 0,
            'rules': reports
        }

    def _get_rule_status(self, rule: BusinessRule) -> str:
        """Get rule status"""
        effectiveness = rule.get_effectiveness_rate()
        
        if rule.usage_count == 0:
            return 'unused'
        elif effectiveness >= 0.7:
            return 'effective'
        elif effectiveness >= 0.4:
            return 'partially_effective'
        else:
            return 'ineffective'

    def get_application_logs(self, rule_id: str = None, limit: int = 50) -> List[Dict]:
        """
        Get rule application logs
        
        Args:
            rule_id: Rule ID (optional)
            limit: Return count limit
        
        Returns:
            Application log list
        """
        logs = self.rule_application_logs
        if rule_id:
            logs = [log for log in logs if log['rule_id'] == rule_id]
        
        return logs[-limit:]


rule_engine = RuleEngine()
