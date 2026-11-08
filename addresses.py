import hashlib
import re
from typing import Optional

TOKENS = ["USDT"]
# key -> (button label, type suffix)
NETWORKS = {
    "BSC": ("BSC / BEP20", "BEP20"),
    "POLYGON": ("Polygon", "POLYGON"),
    "ETH": ("ETH / ERC20", "ERC20"),
    "TRX": ("TRX / TRC20", "TRC20"),
}
EVM_NETWORKS = {"BSC", "POLYGON", "ETH"}

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(text: str) -> Optional[bytes]:
    num = 0
    for ch in text:
        idx = _B58_ALPHABET.find(ch)
        if idx < 0:
            return None
        num = num * 58 + idx
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big")
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + raw


def is_tron_address(address: str) -> bool:
    if len(address) != 34 or not address.startswith("T"):
        return False
    raw = _b58decode(address)
    if not raw or len(raw) != 25 or raw[0] != 0x41:
        return False
    payload, checksum = raw[:21], raw[21:]
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == checksum


def is_valid(network: str, address: str) -> bool:
    if network in EVM_NETWORKS:
        return bool(_EVM_RE.match(address))
    if network == "TRX":
        return is_tron_address(address)
    return False


def normalize(address: str) -> str:
    address = address.strip()
    return address.lower() if address.lower().startswith("0x") else address


def type_label(token: str, network: str) -> str:
    return f"{token}-{NETWORKS[network][1]}"
