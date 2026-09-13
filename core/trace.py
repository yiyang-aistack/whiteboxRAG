"""
RAG full-link tracing module
Defines unified RAGTrace data structure, spanning parsing, rewriting, retrieval, and generation full link
"""
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import config
from service.logger import get_logger
from service.path_safety import sanitize_filename

logger = get_logger('trace')


class RAGTrace:
    """Unified RAG full-link tracing data structure"""

    def __init__(self, trace_id: Optional[str] = None):
        self.trace_id = trace_id or str(uuid.uuid4())
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.stages: List[Dict] = []
        self.intent_info: Optional[Dict] = None
        self.final_answer: Optional[str] = None
        self.sentence_tracing: Optional[List[Dict]] = None
        self.evaluation: Optional[Dict] = None
        self.error: Optional[str] = None
        self.kb_id: Optional[str] = None
        self.query: Optional[str] = None
        self.scenario_id: Optional[str] = None

    def add_stage(self, stage_name: str, input_data: Dict, output_data: Dict, details: Optional[Dict] = None):
        """
        Add stage tracing info

        Args:
            stage_name: Stage name (parse/rewrite/retrieve/generate/evaluate)
            input_data: Input data
            output_data: Output data
            details: Detailed info
        """
        self.stages.append({
            'stage': stage_name,
            'input': input_data,
            'output': output_data,
            'duration': round(time.time() - self.start_time, 4),
            'details': details or {}
        })

    def set_intent_info(self, intent_info: Dict):
        """Set intent info"""
        self.intent_info = intent_info

    def set_final_answer(self, answer: str):
        """Set final answer"""
        self.final_answer = answer

    def set_sentence_tracing(self, sentence_tracing: List[Dict]):
        """Set sentence-level tracing"""
        self.sentence_tracing = sentence_tracing

    def set_evaluation(self, evaluation: Dict):
        """Set evaluation result"""
        self.evaluation = evaluation

    def set_error(self, error: str):
        """Set error message"""
        self.error = error

    def set_context(self, kb_id: str, query: str, scenario_id: Optional[str] = None):
        """Set context info"""
        self.kb_id = kb_id
        self.query = query
        self.scenario_id = scenario_id

    def finish(self):
        """Finish tracing, record end time"""
        self.end_time = time.time()

    @property
    def duration(self) -> float:
        """Calculate total duration"""
        if self.end_time is None:
            return round(time.time() - self.start_time, 4)
        return round(self.end_time - self.start_time, 4)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format"""
        return {
            'trace_id': self.trace_id,
            'kb_id': self.kb_id,
            'query': self.query,
            'scenario_id': self.scenario_id,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'duration': self.duration,
            'intent_info': self.intent_info,
            'stages': self.stages,
            'final_answer': self.final_answer,
            'sentence_tracing': self.sentence_tracing,
            'evaluation': self.evaluation,
            'error': self.error,
            'created_at': datetime.now().isoformat()
        }

    def __repr__(self):
        return f"<RAGTrace trace_id={self.trace_id} stages={len(self.stages)} duration={self.duration:.2f}s>"


class TraceManager:
    """Trace manager"""

    def __init__(self):
        self.trace_dir = Path(config.get('trace.trace_directory', './storage/traces'))
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._active_traces: Dict[str, RAGTrace] = {}

    def create_trace(self, kb_id: str, query: str, scenario_id: Optional[str] = None) -> RAGTrace:
        """Create new trace instance"""
        trace = RAGTrace()
        trace.set_context(kb_id, query, scenario_id)
        self._active_traces[trace.trace_id] = trace
        return trace

    def get_trace(self, trace_id: str) -> Optional[RAGTrace]:
        """Get trace instance"""
        return self._active_traces.get(trace_id)

    def save_trace(self, trace: RAGTrace):
        """Save trace record to file"""
        trace.finish()
        trace_file = self.trace_dir / f'{sanitize_filename(trace.trace_id)}.json'
        try:
            with open(trace_file, 'w', encoding='utf-8') as f:
                json.dump(trace.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save trace record for {trace.trace_id}: {e}")

    def load_trace(self, trace_id: str) -> Optional[RAGTrace]:
        """Load trace record from file"""
        # trace_id arrives from the URL: keep it a single path segment so this cannot read
        # arbitrary *.json files on the host.
        trace_file = self.trace_dir / f'{sanitize_filename(trace_id)}.json'
        if trace_file.exists():
            try:
                with open(trace_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                trace = RAGTrace(data.get('trace_id'))
                trace.start_time = data.get('start_time', time.time())
                trace.end_time = data.get('end_time')
                trace.stages = data.get('stages', [])
                trace.intent_info = data.get('intent_info')
                trace.final_answer = data.get('final_answer')
                trace.sentence_tracing = data.get('sentence_tracing')
                trace.evaluation = data.get('evaluation')
                trace.error = data.get('error')
                trace.kb_id = data.get('kb_id')
                trace.query = data.get('query')
                trace.scenario_id = data.get('scenario_id')
                return trace
            except Exception as e:
                logger.error(f"Failed to load trace record for {trace_id}: {e}")
        return None

    def cleanup(self, max_retention_days: int = 30):
        """Clean up expired trace records"""
        import glob
        cutoff_time = time.time() - max_retention_days * 24 * 3600
        trace_files = glob.glob(str(self.trace_dir / '*.json'))
        deleted_count = 0
        for f in trace_files:
            if os.path.getmtime(f) < cutoff_time:
                os.remove(f)
                deleted_count += 1
        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} expired trace records")


import os

trace_manager = TraceManager()