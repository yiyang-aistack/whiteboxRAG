"""
Typo checker module
Supports common Chinese input method error correction and pinyin similarity check
Typo dictionary stored in config/typo_dict.yaml, supports hot reload
"""
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from service.logger import get_logger

logger = get_logger('typo_checker')


class TypoChecker:
    """Typo checker"""

    def __init__(self):
        # Typo dictionary path (loaded from YAML file for easy maintenance and hot reload)
        self._typo_dict_path = Path(__file__).parent.parent / 'config' / 'typo_dict.yaml'
        self._common_typos: Dict[str, str] = {}
        self._load_typo_dict()

        # NOTE: there used to be a single-character map here ({'则': '这', '那': '哪'})
        # applied to the whole query with str.replace. It corrupted far more than it fixed:
        # 原则/否则/规则/准则 became 原这/否这/规这/准这, and the corrupted string is what BM25
        # and the embedding model were given. The phrase dictionary in config/typo_dict.yaml
        # already covers the intended cases (则个→这个, 则么→这么, ...) without that damage, so
        # corrections are only ever applied to whole dictionary phrases.

    def _load_typo_dict(self):
        """Load typo dictionary from config/typo_dict.yaml"""
        if self._typo_dict_path.exists():
            try:
                import yaml
                with open(self._typo_dict_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}

                # YAML format: {typo: correction}
                self._common_typos = {}
                for typo, correction in data.items():
                    if isinstance(correction, str) and correction:
                        self._common_typos[str(typo)] = correction

                logger.info(f"Loaded {len(self._common_typos)} typo rules from config file")
            except Exception as e:
                logger.error(f"Failed to load typo dictionary: {e}")
                self._common_typos = {}
        else:
            logger.warning(f"Typo dictionary file not found: {self._typo_dict_path}")
            self._common_typos = {}

    def _save_typo_dict(self):
        """Persist current typo dictionary to config/typo_dict.yaml"""
        try:
            import yaml
            with open(self._typo_dict_path, 'w', encoding='utf-8') as f:
                f.write("# Typo dictionary configuration\n# Format: Incorrect spelling: Correct spelling\n\n")
                yaml.dump(self._common_typos, f, allow_unicode=True, indent=2, sort_keys=False)
            logger.info(f"Typo dictionary saved to {self._typo_dict_path}")
        except Exception as e:
            logger.error(f"Failed to save typo dictionary: {e}")

    def reload_typo_dict(self):
        """
        Hot reload typo dictionary: reload from YAML file without restarting service.
        Applies when config/typo_dict.yaml is modified via API for immediate effect.
        """
        self._load_typo_dict()
        logger.info(f"Typo dictionary hot reloaded, current {len(self._common_typos)} rules")
        return len(self._common_typos)

    def check_and_correct(self, text: str) -> Dict:
        """
        Check and correct typos in text

        Args:
            text: Input text

        Returns:
            Correction result dictionary, containing:
                - corrected_text: Corrected text
                - corrections: Correction list, each element contains {original, corrected, position}
                - has_correction: Whether there are corrections
        """
        corrections = []

        # Skip empty keys: they would make the alternation below match at every position.
        phrases = {k: v for k, v in self._common_typos.items() if k}
        if not phrases:
            return {'corrected_text': text, 'corrections': [], 'has_correction': False}

        # Single left-to-right pass over the ORIGINAL text. Longest key first so the
        # alternation prefers the most specific match, and re.sub (unlike a sequence of
        # str.replace calls) never re-scans text it has already substituted — otherwise a
        # shorter key would match inside a replacement (则么办 -> 怎么办 -> 怎怎么办).
        pattern = re.compile(
            '|'.join(re.escape(key) for key in sorted(phrases, key=len, reverse=True))
        )

        def _record(match: 're.Match') -> str:
            original = match.group(0)
            corrections.append({
                'original': original,
                'corrected': phrases[original],
                'position': match.start(),
                'type': 'phrase'
            })
            return phrases[original]

        corrected_text = pattern.sub(_record, text)

        return {
            'corrected_text': corrected_text,
            'corrections': corrections,
            'has_correction': len(corrections) > 0
        }

    def add_typo(self, typo: str, correction: str):
        """
        Add new typo rule

        Args:
            typo: Incorrect spelling
            correction: Correct spelling
        """
        if typo and correction and typo != correction:
            self._common_typos[typo] = correction
            self._save_typo_dict()
            logger.info(f"Added typo rule: {typo} -> {correction}")

    def remove_typo(self, typo: str):
        """
        Delete typo rule

        Args:
            typo: Incorrect spelling
        """
        if typo in self._common_typos:
            del self._common_typos[typo]
            self._save_typo_dict()
            logger.info(f"Deleted typo rule: {typo}")

    def list_typos(self) -> List[Dict]:
        """
        Get all typo rules

        Returns:
            Rule list
        """
        return [{'typo': k, 'correction': v} for k, v in self._common_typos.items()]

    def clear_typos(self):
        """Clear all custom typo rules"""
        self._common_typos.clear()
        logger.info("All custom typo rules cleared")


typo_checker = TypoChecker()