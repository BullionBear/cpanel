"""Utility functions for the application."""
from typing import Optional
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from cryptography.hazmat.backends import default_backend

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

def generate_rsa_keypair(key_size: int = 4096) -> tuple[str, str]:
    """
    Generate an RSA key pair.
    
    Args:
        key_size: The key size in bits (default: 4096)
        
    Returns:
        Tuple of (private_key_pem, public_key_pem)
    """
    # Validate key size
    if key_size < 2048:
        raise ValueError("RSA key size must be at least 2048 bits for security")
    if key_size > 8192:
        raise ValueError("RSA key size cannot exceed 8192 bits")
    
    # Generate RSA private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
        backend=default_backend()
    )
    
    # Serialize private key to PEM format
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')
    
    # Get public key and serialize to PEM format
    public_key = private_key.public_key()
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')
    
    return private_key_pem, public_key_pem


def generate_ed25519_keypair() -> tuple[str, str]:
    """
    Generate an ED25519 key pair.
    
    Returns:
        Tuple of (private_key_pem, public_key_pem)
    """
    # Generate ED25519 private key
    private_key = ed25519.Ed25519PrivateKey.generate()
    
    # Serialize private key to PEM format
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')
    
    # Get public key and serialize to PEM format
    public_key = private_key.public_key()
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')
    
    return private_key_pem, public_key_pem