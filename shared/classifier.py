"""
Matter Classification Engine Module

This module provides email-to-matter classification functionality for the 
Email-to-Matter integration system. It uses multiple weighted signals to 
determine the most relevant matter for an incoming email.

Author: Email-to-Matter Integration System
Version: 1.0.0
"""

import re
import string
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from difflib import SequenceMatcher


@dataclass
class ClassificationResult:
    """
    Data class representing the result of a matter classification.
    
    Attributes:
        matter_id: The unique identifier of the matched matter (None if no match)
        matter_display_number: The display number of the matched matter (None if no match)
        matter_name: The name/title of the matched matter (None if no match)
        confidence_score: Float between 0.0 and 1.0 indicating match confidence
        matched_signals: List of strings explaining which signals contributed to the match
        recommended_action: Either "communication" (high confidence) or "draft_note" (low confidence)
    """
    matter_id: Optional[str] = None
    matter_display_number: Optional[str] = None
    matter_name: Optional[str] = None
    confidence_score: float = 0.0
    matched_signals: List[str] = field(default_factory=list)
    recommended_action: str = "draft_note"


class MatterClassifier:
    """
    Classification engine for matching emails to legal matters.
    
    Uses multiple weighted signals to calculate confidence scores:
    - Matter number exact match (weight: 0.4)
    - Client name match in sender (weight: 0.3)
    - Matter type keywords (weight: 0.15)
    - Matter name keywords (weight: 0.15)
    
    Attributes:
        confidence_threshold: Minimum score to recommend "communication" action
        SIGNAL_WEIGHTS: Dictionary mapping signal names to their weights
    """
    
    # Weighted scoring constants
    SIGNAL_WEIGHTS = {
        "matter_number_match": 0.40,
        "client_name_match": 0.30,
        "matter_type_keywords": 0.15,
        "matter_name_keywords": 0.15
    }
    
    # Matter type keyword mappings for common legal matter types
    MATTER_TYPE_KEYWORDS = {
        "litigation": ["litigation", "lawsuit", "court", "trial", "hearing", "deposition", "discovery"],
        "corporate": ["corporate", "merger", "acquisition", "m&a", "incorporation", "entity", "shareholder"],
        "contract": ["contract", "agreement", "negotiation", "terms", "clause", "amendment"],
        "employment": ["employment", "hr", "labor", "wage", "discrimination", "termination", "hiring"],
        "ip": ["intellectual property", "patent", "trademark", "copyright", "ip", "infringement"],
        "real_estate": ["real estate", "property", "lease", "deed", "title", "zoning"],
        "tax": ["tax", "irs", "taxation", "audit", "deduction", "compliance"],
        "regulatory": ["regulatory", "compliance", "sec", "fda", "investigation"],
        "bankruptcy": ["bankruptcy", "chapter 11", "chapter 7", "restructuring", "debtor", "creditor"]
    }
    
    def __init__(self, confidence_threshold: float = 0.7):
        """
        Initialize the MatterClassifier with a confidence threshold.
        
        Args:
            confidence_threshold: Minimum confidence score (0.0-1.0) to recommend
                                  the "communication" action. Scores below this
                                  threshold will recommend "draft_note".
                                  Default is 0.7.
        """
        self.confidence_threshold = confidence_threshold
    
    def _normalize_text(self, text: Optional[str]) -> str:
        """
        Normalize text for comparison by lowercasing and removing punctuation.
        
        This method prepares text for matching by:
        1. Converting to lowercase
        2. Removing punctuation characters
        3. Normalizing whitespace
        
        Args:
            text: The text to normalize
            
        Returns:
            Normalized text string
        """
        if not text:
            return ""
        
        # Convert to lowercase
        normalized = text.lower()
        
        # Remove punctuation
        normalized = normalized.translate(str.maketrans('', '', string.punctuation))
        
        # Normalize whitespace (multiple spaces to single space)
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        return normalized
    
    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate similarity ratio between two text strings.
        
        Uses SequenceMatcher to compute a similarity score between 0.0 and 1.0.
        
        Args:
            text1: First text string
            text2: Second text string
            
        Returns:
            Similarity ratio (0.0 to 1.0)
        """
        if not text1 or not text2:
            return 0.0
        return SequenceMatcher(None, text1, text2).ratio()
    
    def _extract_signals(
        self, 
        email_text: str, 
        matter: Dict[str, Any]
    ) -> Dict[str, Tuple[float, str]]:
        """
        Extract and calculate individual signal scores for a matter.
        
        Analyzes the email text against matter attributes to calculate
        weighted signal scores for each matching criterion.
        
        Args:
            email_text: Combined normalized email text (subject + body)
            matter: Dictionary containing matter attributes:
                - matter_id: Unique identifier
                - matter_number: Display number (e.g., "2024-001")
                - matter_name: Name/title of the matter
                - client_name: Name of the client
                - matter_type: Type/category of matter
                
        Returns:
            Dictionary mapping signal names to tuples of (score, description)
        """
        signals = {}
        
        # Signal 1: Matter number exact match (weight: 0.4)
        matter_number = matter.get("matter_number", "") or matter.get("display_number", "")
        if matter_number:
            normalized_number = self._normalize_text(matter_number)
            # Check for exact match or match with common separators removed
            number_variants = [
                normalized_number,
                normalized_number.replace("-", ""),
                normalized_number.replace(" ", ""),
                normalized_number.replace("-", " ")
            ]
            
            number_score = 0.0
            for variant in number_variants:
                if variant in email_text:
                    number_score = 1.0
                    break
            
            if number_score > 0:
                signals["matter_number_match"] = (
                    number_score,
                    f"Matter number '{matter_number}' found in email"
                )
        
        # Signal 2: Client name match in sender/from (weight: 0.3)
        client_name = matter.get("client_name", "")
        if client_name:
            normalized_client = self._normalize_text(client_name)
            # Check for client name as a word boundary match
            client_parts = normalized_client.split()
            
            # Full name match
            if normalized_client in email_text:
                signals["client_name_match"] = (
                    1.0,
                    f"Client name '{client_name}' found in email"
                )
            # Partial match (last name or significant part)
            elif len(client_parts) > 1:
                # Check if last name matches
                last_name = client_parts[-1]
                if len(last_name) > 2 and re.search(r'\b' + re.escape(last_name) + r'\b', email_text):
                    signals["client_name_match"] = (
                        0.7,
                        f"Client last name '{last_name}' found in email"
                    )
        
        # Signal 3: Matter type keywords (weight: 0.15)
        matter_type = matter.get("matter_type", "").lower()
        if matter_type and matter_type in self.MATTER_TYPE_KEYWORDS:
            keywords = self.MATTER_TYPE_KEYWORDS[matter_type]
            matched_keywords = []
            
            for keyword in keywords:
                normalized_keyword = self._normalize_text(keyword)
                if normalized_keyword in email_text:
                    matched_keywords.append(keyword)
            
            if matched_keywords:
                # Score based on number of matched keywords (max 1.0)
                type_score = min(1.0, len(matched_keywords) / 2)
                signals["matter_type_keywords"] = (
                    type_score,
                    f"Matter type keywords matched: {', '.join(matched_keywords[:3])}"
                )
        
        # Signal 4: Matter name keywords (weight: 0.15)
        matter_name = matter.get("matter_name", "")
        if matter_name:
            normalized_matter_name = self._normalize_text(matter_name)
            
            # Split matter name into significant words (excluding common words)
            common_words = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for"}
            name_words = [w for w in normalized_matter_name.split() 
                         if w not in common_words and len(w) > 2]
            
            matched_words = []
            for word in name_words:
                if re.search(r'\b' + re.escape(word) + r'\b', email_text):
                    matched_words.append(word)
            
            if matched_words:
                # Score based on proportion of matched words
                name_score = min(1.0, len(matched_words) / max(3, len(name_words) * 0.5))
                signals["matter_name_keywords"] = (
                    name_score,
                    f"Matter name keywords matched: {', '.join(matched_words[:5])}"
                )
        
        return signals
    
    def _calculate_confidence(
        self, 
        signals: Dict[str, Tuple[float, str]]
    ) -> Tuple[float, List[str]]:
        """
        Calculate overall confidence score from individual signals.
        
        Sums weighted signal scores to produce a final confidence score
        between 0.0 and 1.0.
        
        Args:
            signals: Dictionary of signal names to (score, description) tuples
            
        Returns:
            Tuple of (confidence_score, list_of_matched_signal_descriptions)
        """
        total_score = 0.0
        matched_signals = []
        
        for signal_name, (score, description) in signals.items():
            weight = self.SIGNAL_WEIGHTS.get(signal_name, 0.0)
            weighted_score = score * weight
            total_score += weighted_score
            
            if score > 0:
                matched_signals.append(description)
        
        # Ensure score is between 0.0 and 1.0
        total_score = min(1.0, max(0.0, total_score))
        
        return total_score, matched_signals
    
    def classify(
        self,
        email_subject: str,
        email_body: str,
        sender_email: str,
        sender_name: str,
        matters_list: List[Dict[str, Any]]
    ) -> ClassificationResult:
        """
        Classify an email against a list of matters and return the best match.
        
        Analyzes the email using multiple signals to find the most relevant
        matter from the provided list.
        
        Args:
            email_subject: The subject line of the email
            email_body: The body content of the email
            sender_email: The email address of the sender
            sender_name: The display name of the sender
            matters_list: List of matter dictionaries to match against.
                         Each matter should contain:
                         - matter_id: Unique identifier
                         - matter_number or display_number: Matter number
                         - matter_name: Name of the matter
                         - client_name: Client name
                         - matter_type: Type of matter (optional)
                         
        Returns:
            ClassificationResult containing the best match and confidence details
        """
        # Combine all email text for analysis
        combined_text = f"{email_subject} {email_body} {sender_email} {sender_name}"
        normalized_email_text = self._normalize_text(combined_text)
        
        # If no matters provided, return empty result
        if not matters_list:
            return ClassificationResult(
                confidence_score=0.0,
                matched_signals=["No matters available for matching"],
                recommended_action="draft_note"
            )
        
        # Score each matter
        matter_scores = []
        
        for matter in matters_list:
            # Extract signals for this matter
            signals = self._extract_signals(normalized_email_text, matter)
            
            # Calculate confidence score
            confidence, matched_signals = self._calculate_confidence(signals)
            
            matter_scores.append({
                "matter": matter,
                "confidence": confidence,
                "signals": matched_signals,
                "raw_signals": signals
            })
        
        # Sort by confidence score (descending)
        matter_scores.sort(key=lambda x: x["confidence"], reverse=True)
        
        # Get the best match
        best_match = matter_scores[0]
        best_matter = best_match["matter"]
        best_confidence = best_match["confidence"]
        best_signals = best_match["signals"]
        
        # Determine recommended action
        recommended_action = "communication" if best_confidence >= self.confidence_threshold else "draft_note"
        
        # Build result
        result = ClassificationResult(
            matter_id=best_matter.get("matter_id") or best_matter.get("id"),
            matter_display_number=best_matter.get("matter_number") or best_matter.get("display_number"),
            matter_name=best_matter.get("matter_name") or best_matter.get("name"),
            confidence_score=round(best_confidence, 4),
            matched_signals=best_signals if best_signals else ["No strong matching signals found"],
            recommended_action=recommended_action
        )
        
        return result
    
    def classify_with_details(
        self,
        email_subject: str,
        email_body: str,
        sender_email: str,
        sender_name: str,
        matters_list: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Classify an email and return detailed scoring information.
        
        Similar to classify() but returns additional debug information
        including scores for all matters, not just the best match.
        
        Args:
            email_subject: The subject line of the email
            email_body: The body content of the email
            sender_email: The email address of the sender
            sender_name: The display name of the sender
            matters_list: List of matter dictionaries to match against
            
        Returns:
            Dictionary containing:
                - result: The ClassificationResult for the best match
                - all_scores: List of scores for all matters
                - threshold: The confidence threshold used
        """
        # Combine all email text for analysis
        combined_text = f"{email_subject} {email_body} {sender_email} {sender_name}"
        normalized_email_text = self._normalize_text(combined_text)
        
        # Score each matter
        all_scores = []
        
        for matter in matters_list:
            signals = self._extract_signals(normalized_email_text, matter)
            confidence, matched_signals = self._calculate_confidence(signals)
            
            all_scores.append({
                "matter_id": matter.get("matter_id") or matter.get("id"),
                "matter_number": matter.get("matter_number") or matter.get("display_number"),
                "matter_name": matter.get("matter_name") or matter.get("name"),
                "confidence": round(confidence, 4),
                "signals": matched_signals,
                "signal_breakdown": {
                    name: {"score": score, "description": desc}
                    for name, (score, desc) in signals.items()
                }
            })
        
        # Sort by confidence
        all_scores.sort(key=lambda x: x["confidence"], reverse=True)
        
        # Get best result using standard classify
        result = self.classify(email_subject, email_body, sender_email, sender_name, matters_list)
        
        return {
            "result": result,
            "all_scores": all_scores,
            "threshold": self.confidence_threshold
        }


# Convenience function for direct usage
def classify_email(
    email_subject: str,
    email_body: str,
    sender_email: str,
    sender_name: str,
    matters_list: List[Dict[str, Any]],
    confidence_threshold: float = 0.7
) -> ClassificationResult:
    """
    Convenience function to classify an email without instantiating the class.
    
    Args:
        email_subject: The subject line of the email
        email_body: The body content of the email
        sender_email: The email address of the sender
        sender_name: The display name of the sender
        matters_list: List of matter dictionaries to match against
        confidence_threshold: Minimum score for "communication" recommendation
        
    Returns:
        ClassificationResult with the best matching matter
    """
    classifier = MatterClassifier(confidence_threshold=confidence_threshold)
    return classifier.classify(email_subject, email_body, sender_email, sender_name, matters_list)
