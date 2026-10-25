"""Corps de requête compressé par le navigateur (connexion lente, 25 oct.).

Le navigateur peut envoyer un corps en gzip ou en deflate (`CompressionStream`), avec
`Content-Encoding`. On le décompresse ici, avec un plafond sur la taille décompressée :
une « bombe » (quelques Ko qui gonflent en Go) est refusée dès que le plafond est dépassé,
sans jamais allouer plus que lui.
"""

from __future__ import annotations

import zlib

from fastapi import HTTPException

_WBITS = {"gzip": 16 + zlib.MAX_WBITS, "x-gzip": 16 + zlib.MAX_WBITS, "deflate": zlib.MAX_WBITS}
_CHUNK = 64 * 1024


def decoded_body(body: bytes, content_encoding: str | None, max_bytes: int) -> bytes:
    """Le corps tel qu'envoyé avant compression. Sans `Content-Encoding` (ou `identity`) : tel
    quel. 413 au-delà de `max_bytes` (compressé ou décompressé), 415 pour un codage inconnu,
    400 pour un flux illisible ou tronqué."""
    encoding = (content_encoding or "identity").strip().lower()
    if encoding in ("", "identity"):
        return body
    wbits = _WBITS.get(encoding)
    if wbits is None:
        raise HTTPException(status_code=415, detail=f"codage du corps non accepté : {encoding}")
    if len(body) > max_bytes:
        raise HTTPException(status_code=413, detail="corps trop grand")
    inflater = zlib.decompressobj(wbits)
    out = bytearray()
    data = body
    try:
        while data:
            # Jamais plus que le plafond + 1 octet d'un coup : la bombe s'arrête là.
            chunk = inflater.decompress(data, max(1, min(_CHUNK, max_bytes + 1 - len(out))))
            out += chunk
            if len(out) > max_bytes:
                raise HTTPException(status_code=413, detail="corps décompressé trop grand")
            data = inflater.unconsumed_tail
            if not chunk and data:
                break  # rien ne sort alors qu'il reste des octets : flux incohérent
        out += inflater.flush()
    except zlib.error as exc:
        raise HTTPException(status_code=400, detail="corps compressé illisible") from exc
    if len(out) > max_bytes:
        raise HTTPException(status_code=413, detail="corps décompressé trop grand")
    if not inflater.eof:
        raise HTTPException(status_code=400, detail="corps compressé tronqué")
    return bytes(out)
