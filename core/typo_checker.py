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

        # Single-character typo mapping (lightweight rules, kept in code)
        self._char_typos = {
            '则': '这',
            '那': '哪',
        }

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
        corrected_text = text

        for typo, correct in sorted(self._common_typos.items(), key=lambda x: -len(x[0])):
            if typo in corrected_text:
                positions = [m.start() for m in re.finditer(re.escape(typo), corrected_text)]
                for pos in positions:
                    corrections.append({
                        'original': typo,
                        'corrected': correct,
                        'position': pos,
                        'type': 'phrase'
                    })
                corrected_text = corrected_text.replace(typo, correct)

        for char, correct_char in self._char_typos.items():
            if char != correct_char and char in corrected_text:
                positions = [i for i, c in enumerate(corrected_text) if c == char]
                for pos in positions:
                    corrections.append({
                        'original': char,
                        'corrected': correct_char,
                        'position': pos,
                        'type': 'character'
                    })
                corrected_text = corrected_text.replace(char, correct_char)

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