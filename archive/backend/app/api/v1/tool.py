"""Tool router - convenient utility tools."""
from enum import Enum
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from app.utils import generate_rsa_keypair, generate_ed25519_keypair

router = APIRouter(prefix="/tool", tags=["tools"])


class KeyType(str, Enum):
    """Supported key types for generation."""
    RSA = "RSA"
    ED25519 = "ED25519"


class KeyGenerateRequest(BaseModel):
    """Key generation request model."""
    key_type: KeyType = Field(..., description="Type of key to generate (RSA or ED25519)")
    key_size: Optional[int] = Field(
        default=4096,
        description="Key size in bits (only applicable for RSA, ignored for ED25519). Common values: 2048, 4096"
    )


class KeyGenerateResponse(BaseModel):
    """Key generation response model."""
    key_type: str
    private_key: str = Field(..., description="Private key in PEM format")
    public_key: str = Field(..., description="Public key in PEM format")





@router.post("/keygen", response_model=KeyGenerateResponse)
async def generate_keypair(request: KeyGenerateRequest):
    """
    Generate a cryptographic key pair.
    
    Supports RSA and ED25519 key types:
    - **RSA**: Widely compatible, configurable key size (2048-8192 bits)
    - **ED25519**: Modern, fast, compact keys (fixed 256-bit)
    
    Returns the private and public keys in PEM format.
    """
    try:
        if request.key_type == KeyType.RSA:
            key_size = request.key_size or 4096
            private_key, public_key = generate_rsa_keypair(key_size)
        elif request.key_type == KeyType.ED25519:
            private_key, public_key = generate_ed25519_keypair()
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported key type: {request.key_type}"
            )
        
        return KeyGenerateResponse(
            key_type=request.key_type.value,
            private_key=private_key,
            public_key=public_key
        )
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate key pair: {str(e)}"
        )
