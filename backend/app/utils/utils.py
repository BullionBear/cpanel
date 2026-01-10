"""Utility functions for the application."""
from typing import Optional


def trim_decimal_string(value: Optional[str]) -> str:
    """
    Trim trailing zeros from a decimal string.
    
    This function removes trailing zeros after the decimal point and removes
    the decimal point itself if all decimal digits are zeros.
    
    Examples:
        "5.50400000" -> "5.504"
        "831.39000000" -> "831.39"
        "4.23000000" -> "4.23"
        "100.00000000" -> "100"
        "100" -> "100"
        "0.5" -> "0.5"
        "0.50" -> "0.5"
        None -> ""
        "" -> ""
    
    Args:
        value: Decimal string to trim, or None
    
    Returns:
        Trimmed decimal string. Returns empty string if input is None or empty.
    """
    if value is None:
        return ""
    
    if not isinstance(value, str):
        value = str(value)
    
    # Remove trailing zeros after decimal point
    if '.' in value:
        # Remove trailing zeros and decimal point if all zeros
        value = value.rstrip('0').rstrip('.')
    
    return value
