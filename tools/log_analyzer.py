"""
Log analysis tool class - encapsulates log parsing, statistical analysis and report generation

Provides analysis capabilities for rule engine, semantic detection, retrieval workflow and other logs,
supports generating text reports and HTML visualization reports.

Usage example:
    from tools.log_analyzer import LogAnalyzer
    
    # Create analyzer instance
    analyzer = LogAnalyzer(log_dir="./storage/logs")
    
    # Execute analysis
    analyzer.analyze()
    
    # Generate text report
    text_report = analyzer.generate_text_report()
    print(text_report)
    
    # Generate HTML report
    html_path = analyzer.generate_html_report(output_path="./report.html")
    
    # Get analysis data
    rule_stats = analyzer.get_rule_stats()
    contradiction_stats = analyzer.get_contradiction_stats()
"""

import os
import re
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any


@dataclass
class RuleEffectStats:
    """Rule effectiveness statistics data"""
    rule_id: str
    effective_count: int = 0
    ineffective_count: int = 0
    
    @property
    def total_count(self) -> int:
        return self.effective_count + self.ineffective_count
    
    @property
    def effectiveness_rate(self) -> float:
        if self.total_count == 0:
            return 0.0
        return self.effective_count / self.total_count * 100
    
    @property
    def status(self) -> str:
        if self.effectiveness_rate >= 70:
            return "effective"
        elif self.effectiveness_rate >= 40:
            return "partial"
        else:
            return "ineffective"
    
    @property
    def status_text(self) -> str:
        status_map = {
            "effective": "Effective",
            "partial": "Partial Effective",
            "ineffective": "Invalid"
        }
        return status_map[self.status]


@dataclass
class ContradictionStats:
    """Semantic contradiction statistics data"""
    total_count: int = 0
    by_type: Dict[str, int] = None
    by_severity: Dict[str, int] = None
    
    def __post_init__(self):
        if self.by_type is None:
            self.by_type = {}
        if self.by_severity is None:
            self.by_severity = {}
    
    def add_contradiction(self, contradiction_type: str, severity: str):
        """Add contradiction record"""
        self.total_count += 1
        self.by_type[contradiction_type] = self.by_type.get(contradiction_type, 0) + 1
        self.by_severity[severity] = self.by_severity.get(severity, 0) + 1
    
    def get_severity_percentage(self, severity: str) -> float:
        """Get severity percentage"""
        if self.total_count == 0:
            return 0.0
        return self.by_severity.get(severity, 0) / self.total_count * 100
    
    def get_type_percentage(self, contradiction_type: str) -> float:
        """Get type percentage"""
        if self.total_count == 0:
            return 0.0
        return self.by_type.get(contradiction_type, 0) / self.total_count * 100


@dataclass
class RetrievalStats:
    """Retrieval statistics data"""
    total_queries: int = 0
    queries_with_results: int = 0
    by_mode: Dict[str, int] = None
    
    def __post_init__(self):
        if self.by_mode is None:
            self.by_mode = {}
    
    @property
    def success_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.queries_with_results / self.total_queries * 100
    
    def add_query(self, mode: str, has_results: bool = False):
        """Add query record"""
        self.total_queries += 1
        self.by_mode[mode] = self.by_mode.get(mode, 0) + 1
        if has_results:
            self.queries_with_results += 1


class LogParser:
    """Log parser base class"""
    
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.raw_lines: List[str] = []
    
    def load(self) -> bool:
        """Load log file"""
        if not self.log_file.exists():
            return False
        with open(self.log_file, 'r', encoding='utf-8') as f:
            self.raw_lines = f.readlines()
        return True
    
    def parse(self) -> Any:
        """Parse log, return statistics data"""
        raise NotImplementedError("Subclasses must implement parse method")


class RuleEngineLogParser(LogParser):
    """Rule engine log parser"""
    
    RULE_EFFECT_PATTERN = re.compile(
        r'\[Rule Engine\] rule evaluation completed，Rule ID: (\S+), Effective count: (\d+), Ineffective count: (\d+)'
    )
    
    RULE_APPLICATION_PATTERN = re.compile(
        r'\[Rule Engine\] rule application logged，Rule ID: (\S+), Query: ([^\n]+?), Action: ([^\n]+?), Result summary: ([^\n]+?)'
    )
    
    def parse(self) -> Dict[str, RuleEffectStats]:
        """Parse rule engine log, return rule effectiveness statistics"""
        if not self.load():
            return {}
        
        rule_stats: Dict[str, RuleEffectStats] = {}
        
        for line in self.raw_lines:
            line = line.strip()
            if not line:
                continue
            
            match = self.RULE_EFFECT_PATTERN.search(line)
            if match:
                rule_id = match.group(1)
                if rule_id not in rule_stats:
                    rule_stats[rule_id] = RuleEffectStats(rule_id=rule_id)
                rule_stats[rule_id].effective_count += int(match.group(2))
                rule_stats[rule_id].ineffective_count += int(match.group(3))
        
        return rule_stats


class SentenceTracingLogParser(LogParser):
    """Semantic detection log parser"""
    
    CONTRADICTION_PATTERN = re.compile(
        r'\[Semantic Detection\] contradiction found，Type: ([^,\s]+), Severity: ([^,\s]+)'
    )
    
    def parse(self) -> ContradictionStats:
        """Parse semantic detection log, return contradiction statistics"""
        stats = ContradictionStats()
        
        if not self.load():
            return stats
        
        for line in self.raw_lines:
            line = line.strip()
            if not line:
                continue
            
            match = self.CONTRADICTION_PATTERN.search(line)
            if match:
                contradiction_type = match.group(1)
                severity = match.group(2)
                stats.add_contradiction(contradiction_type, severity)
        
        return stats


class RetrieverLogParser(LogParser):
    """Retrieval log parser"""
    
    QUERY_START_PATTERN = re.compile(
        r'\[Retrieval Process\] start retrieval，KB ID: ([^,\s]+), Query: ([^\n]+?), Mode: ([^,\s]+)'
    )
    
    QUERY_END_PATTERN = re.compile(
        r'\[Retrieval Process\] retrieval completed，Return (\d+) results, Has results: (\S+)'
    )
    
    def parse(self) -> RetrievalStats:
        """Parse retrieval log, return retrieval statistics"""
        stats = RetrievalStats()
        
        if not self.load():
            return stats
        
        for line in self.raw_lines:
            line = line.strip()
            if not line:
                continue
            
            match = self.QUERY_START_PATTERN.search(line)
            if match:
                mode = match.group(3)
                stats.add_query(mode)
            
            match = self.QUERY_END_PATTERN.search(line)
            if match:
                has_results = match.group(2) == 'True'
                if has_results:
                    stats.queries_with_results += 1
        
        return stats


class LogAnalyzer:
    """
    Log analyzer - integrates various log parsers, provides a unified analysis interface
    
    Attributes:
        log_dir: Log directory path
        rule_stats: Rule effectiveness statistics
        contradiction_stats: Semantic contradiction statistics
        retrieval_stats: Retrieval statistics
    """
    
    def __init__(self, log_dir: str = "./storage/logs"):
        self.log_dir = Path(log_dir)
        self.rule_stats: Dict[str, RuleEffectStats] = {}
        self.contradiction_stats: ContradictionStats = ContradictionStats()
        self.retrieval_stats: RetrievalStats = RetrievalStats()
    
    def analyze(self) -> "LogAnalyzer":
        """
        Execute the complete analysis workflow
        
        Returns:
            self - for chained calls
        """
        self._parse_rule_engine_logs()
        self._parse_sentence_tracing_logs()
        self._parse_retriever_logs()
        return self
    
    def _parse_rule_engine_logs(self):
        """Parse rule engine logs"""
        parser = RuleEngineLogParser(self.log_dir / "rule_engine.log")
        self.rule_stats = parser.parse()
    
    def _parse_sentence_tracing_logs(self):
        """Parse semantic detection logs"""
        parser = SentenceTracingLogParser(self.log_dir / "sentence_tracing.log")
        self.contradiction_stats = parser.parse()
    
    def _parse_retriever_logs(self):
        """Parse retrieval logs"""
        parser = RetrieverLogParser(self.log_dir / "retriever.log")
        self.retrieval_stats = parser.parse()
    
    def get_rule_stats(self) -> Dict[str, RuleEffectStats]:
        """Get rule effectiveness statistics"""
        return self.rule_stats
    
    def get_contradiction_stats(self) -> ContradictionStats:
        """Get semantic contradiction statistics"""
        return self.contradiction_stats
    
    def get_retrieval_stats(self) -> RetrievalStats:
        """Get retrieval statistics"""
        return self.retrieval_stats
    
    def get_summary(self) -> Dict[str, Any]:
        """Get analysis summary"""
        return {
            "rule_engine": {
                "total_rules": len(self.rule_stats),
                "avg_effectiveness": self._calculate_avg_effectiveness(),
                "effective_rules": sum(1 for s in self.rule_stats.values() if s.status == "effective"),
                "partial_rules": sum(1 for s in self.rule_stats.values() if s.status == "partial"),
                "ineffective_rules": sum(1 for s in self.rule_stats.values() if s.status == "ineffective")
            },
            "contradiction": {
                "total_count": self.contradiction_stats.total_count,
                "by_severity": self.contradiction_stats.by_severity,
                "by_type": self.contradiction_stats.by_type
            },
            "retrieval": {
                "total_queries": self.retrieval_stats.total_queries,
                "success_rate": self.retrieval_stats.success_rate,
                "by_mode": self.retrieval_stats.by_mode
            }
        }
    
    def _calculate_avg_effectiveness(self) -> float:
        """Calculate average effectiveness rate"""
        if not self.rule_stats:
            return 0.0
        total_effective = sum(s.effective_count for s in self.rule_stats.values())
        total = sum(s.total_count for s in self.rule_stats.values())
        return total_effective / total * 100 if total > 0 else 0.0
    
    def generate_text_report(self) -> str:
        """
        Generate text format report
        
        Returns:
            str - formatted text report
        """
        report = []
        report.append("=" * 70)
        report.append("whiteBoxRAG Log Analysis Report")
        report.append("=" * 70)
        report.append(f"Generated Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        report.append("📊 Rule Engine Analysis")
        report.append("-" * 50)
        if self.rule_stats:
            for rule_id, stats in self.rule_stats.items():
                report.append(f"  Rule {rule_id}:")
                report.append(f"    Effective Count: {stats.effective_count}")
                report.append(f"    Ineffective Count: {stats.ineffective_count}")
                report.append(f"    Effectiveness Rate: {stats.effectiveness_rate:.2f}%")
                
                status_emoji = {"effective": "✅", "partial": "⚠️", "ineffective": "❌"}
                report.append(f"    Status: {status_emoji.get(stats.status, '⚪')} {stats.status_text}")
                report.append("")
        else:
            report.append("  No rule effectiveness data available")
            report.append("")
        
        report.append("🔍 Semantic Contradiction Analysis")
        report.append("-" * 50)
        if self.contradiction_stats.total_count > 0:
            report.append(f"  Total Contradictions Count: {self.contradiction_stats.total_count}")
            report.append("")
            
            report.append("  Type Distribution by:")
            for ct, count in self.contradiction_stats.by_type.items():
                percentage = self.contradiction_stats.get_type_percentage(ct)
                bar = "█" * int(percentage / 10) + "░" * (10 - int(percentage / 10))
                report.append(f"    {ct}: {count} ({percentage:.1f}%) {bar}")
            report.append("")
            
            report.append("  Severity Distribution by:")
            severity_colors = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}
            for severity, count in self.contradiction_stats.by_severity.items():
                percentage = self.contradiction_stats.get_severity_percentage(severity)
                color = severity_colors.get(severity, '⚪')
                report.append(f"    {color} {severity}: {count} ({percentage:.1f}%)")
            report.append("")
        else:
            report.append("  No semantic contradictions data available")
            report.append("")
        
        report.append("🔄 Retrieval Analysis")
        report.append("-" * 50)
        if self.retrieval_stats.total_queries > 0:
            report.append(f"  Total Queries Count: {self.retrieval_stats.total_queries}")
            report.append(f"  Queries with Results Count: {self.retrieval_stats.queries_with_results}")
            report.append(f"  Success Rate: {self.retrieval_stats.success_rate:.2f}%")
            report.append("")
            
            report.append("  Mode Distribution by:")
            for mode, count in self.retrieval_stats.by_mode.items():
                percentage = count / self.retrieval_stats.total_queries * 100
                report.append(f"    {mode}: {count} ({percentage:.1f}%)")
        else:
            report.append("  No retrieval data available")
            report.append("")
        
        report.append("=" * 70)
        return "\n".join(report)
    
    def generate_html_report(self, output_path: str = "./storage/log_report.html") -> str:
        """
        Generate HTML visualization report
        
        Args:
            output_path: Output file path
        
        Returns:
            str - generated HTML file path
        """
        html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>whiteBoxRAG Log Analysis Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{ text-align: center; color: white; margin-bottom: 30px; }}
        .header h1 {{ font-size: 2.5rem; margin-bottom: 10px; }}
        .header p {{ font-size: 1rem; opacity: 0.9; }}
        .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: white; border-radius: 16px; padding: 24px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); }}
        .card h3 {{ color: #333; margin-bottom: 20px; font-size: 1.3rem; border-bottom: 2px solid #667eea; padding-bottom: 10px; }}
        .stat-row {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid #eee; }}
        .stat-label {{ color: #666; font-size: 0.9rem; }}
        .stat-value {{ font-weight: bold; font-size: 1.2rem; color: #333; }}
        .charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; }}
        .chart-card {{ background: white; border-radius: 16px; padding: 24px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); }}
        .chart-card h3 {{ color: #333; margin-bottom: 20px; font-size: 1.3rem; border-bottom: 2px solid #667eea; padding-bottom: 10px; }}
        .chart-container {{ height: 300px; }}
        .rule-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        .rule-table th, .rule-table td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
        .rule-table th {{ background: #f8f9fa; color: #666; font-weight: 600; }}
        .status-badge {{ padding: 4px 12px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; }}
        .status-effective {{ background: #d4edda; color: #155724; }}
        .status-partial {{ background: #fff3cd; color: #856404; }}
        .status-ineffective {{ background: #f8d7da; color: #721c24; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 whiteBoxRAG Log Analysis Report</h1>
            <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
        
        <div class="cards">
            <div class="card">
                <h3>📈 Rule Engine Overview</h3>
                <div class="stat-row"><span class="stat-label">Total Rules</span><span class="stat-value">{len(self.rule_stats)}</span></div>
                <div class="stat-row"><span class="stat-label">Average Effectiveness</span><span class="stat-value">{self._calculate_avg_effectiveness():.1f}%</span></div>
                <div class="stat-row"><span class="stat-label">Effective Rules</span><span class="stat-value">{sum(1 for s in self.rule_stats.values() if s.status == 'effective')}</span></div>
                <div class="stat-row"><span class="stat-label">Partial Effective Rules</span><span class="stat-value">{sum(1 for s in self.rule_stats.values() if s.status == 'partial')}</span></div>
                <div class="stat-row"><span class="stat-label">Ineffective Rules</span><span class="stat-value">{sum(1 for s in self.rule_stats.values() if s.status == 'ineffective')}</span></div>
            </div>
            
            <div class="card">
                <h3>🔍 Semantic Contradictions Overview</h3>
                <div class="stat-row"><span class="stat-label">Total Count</span><span class="stat-value">{self.contradiction_stats.total_count}</span></div>
                <div class="stat-row"><span class="stat-label">High Severity</span><span class="stat-value">{self.contradiction_stats.by_severity.get('high', 0)}</span></div>
                <div class="stat-row"><span class="stat-label">Medium Severity</span><span class="stat-value">{self.contradiction_stats.by_severity.get('medium', 0)}</span></div>
                <div class="stat-row"><span class="stat-label">Low Severity</span><span class="stat-value">{self.contradiction_stats.by_severity.get('low', 0)}</span></div>
            </div>
            
            <div class="card">
                <h3>🔄 Retrieval Overview</h3>
                <div class="stat-row"><span class="stat-label">Total Queries</span><span class="stat-value">{self.retrieval_stats.total_queries}</span></div>
                <div class="stat-row"><span class="stat-label">Queries with Results</span><span class="stat-value">{self.retrieval_stats.queries_with_results}</span></div>
                <div class="stat-row"><span class="stat-label">Success Rate</span><span class="stat-value">{self.retrieval_stats.success_rate:.1f}%</span></div>
                <div class="stat-row"><span class="stat-label">Hybrid Retrieval</span><span class="stat-value">{self.retrieval_stats.by_mode.get('hybrid', 0)}</span></div>
                <div class="stat-row"><span class="stat-label">Vector Retrieval</span><span class="stat-value">{self.retrieval_stats.by_mode.get('vector', 0)}</span></div>
            </div>
        </div>
        
        <div class="charts">
            <div class="chart-card">
                <h3>📊 Rule Effectiveness Distribution</h3>
                <div class="chart-container">
                    <canvas id="ruleChart"></canvas>
                </div>
            </div>
            
            <div class="chart-card">
                <h3>🔍 Semantic Contradictions Type Distribution</h3>
                <div class="chart-container">
                    <canvas id="contradictionTypeChart"></canvas>
                </div>
            </div>
            
            <div class="chart-card">
                <h3>⚠️ Semantic Contradictions Severity Distribution</h3>
                <div class="chart-container">
                    <canvas id="contradictionSeverityChart"></canvas>
                </div>
            </div>
            
            <div class="chart-card">
                <h3>🔄 Retrieval Mode Distribution</h3>
                <div class="chart-container">
                    <canvas id="retrievalModeChart"></canvas>
                </div>
            </div>
        </div>
        
        <div class="chart-card">
            <h3>📋 Rule Details List</h3>
            <table class="rule-table">
                <thead>
                    <tr><th>Rule ID</th><th>Effective Count</th><th>Invalid Count</th><th>Effectiveness</th><th>Status</th></tr>
                </thead>
                <tbody>
                    {self._generate_rule_table_rows()}
                </tbody>
            </table>
        </div>
    </div>
    
    <script>
        new Chart(document.getElementById('ruleChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(list(self.rule_stats.keys()))},
                datasets: [
                    {{ label: 'Effective Count', data: {json.dumps([s.effective_count for s in self.rule_stats.values()])}, backgroundColor: '#28a745' }},
                    {{ label: 'Invalid Count', data: {json.dumps([s.ineffective_count for s in self.rule_stats.values()])}, backgroundColor: '#dc3545' }}
                ]
            }},
            options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
        }});
        
        new Chart(document.getElementById('contradictionTypeChart'), {{
            type: 'doughnut',
            data: {{
                labels: {json.dumps(list(self.contradiction_stats.by_type.keys()))},
                datasets: [{{
                    data: {json.dumps(list(self.contradiction_stats.by_type.values()))},
                    backgroundColor: ['#dc3545', '#fd7e14', '#ffc107', '#17a2b8', '#6610f2']
                }}]
            }},
            options: {{ responsive: true }}
        }});
        
        new Chart(document.getElementById('contradictionSeverityChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(list(self.contradiction_stats.by_severity.keys()))},
                datasets: [{{
                    label: 'Contradiction Count',
                    data: {{json.dumps(list(self.contradiction_stats.by_severity.values()))}},
                    backgroundColor: {{high: '#dc3545', medium: '#ffc107', low: '#28a745'}}
                }}]
            }},
            options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
        }});
        
        new Chart(document.getElementById('retrievalModeChart'), {{
            type: 'pie',
            data: {{
                labels: {json.dumps(list(self.retrieval_stats.by_mode.keys()))},
                datasets: [{{
                    data: {{json.dumps(list(self.retrieval_stats.by_mode.values()))}},
                    backgroundColor: ['#007bff', '#28a745', '#ffc107']
                }}]
            }},
            options: {{ responsive: true }}
        }});
    </script>
</body>
</html>
"""
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        return str(output_path)
    
    def _generate_rule_table_rows(self) -> str:
        """Generate rule table rows"""
        rows = []
        for rule_id, stats in self.rule_stats.items():
            rows.append(f"""
            <tr>
                <td>{rule_id}</td>
                <td>{stats.effective_count}</td>
                <td>{stats.ineffective_count}</td>
                <td>{stats.effectiveness_rate:.1f}%</td>
                <td><span class="status-badge status-{stats.status}">{stats.status_text}</span></td>
            </tr>
            """)
        return "\n".join(rows) if rows else "<tr><td colspan='5' style='text-align: center;'>No data available</td></tr>"


if __name__ == '__main__':
    analyzer = LogAnalyzer()
    analyzer.analyze()
    
    print("Generating text report...")
    text_report = analyzer.generate_text_report()
    print(text_report)
    
    print("\nGenerating HTML report...")
    html_path = analyzer.generate_html_report()
    abs_html_path = str(Path(html_path).resolve())
    print(f"HTML report generated: {abs_html_path}")
    
    print("\nAnalysis summary:")
    summary = analyzer.get_summary()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    
    print("\nOpening report...")
    os.startfile(abs_html_path)
