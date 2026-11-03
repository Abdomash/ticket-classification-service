"""Post the sample tickets to the API, wait until each one is classified or failed, and print them.

Usage: python scripts/load_samples.py [base_url]    (default: http://localhost:8000)
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx

SAMPLES = Path(__file__).parent.parent / "samples" / "tickets.json"
WAIT_SECONDS = 120


async def main(base_url: str) -> None:
    samples = json.loads(SAMPLES.read_text())

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        for sample in samples:
            response = await client.post("/tickets", json=sample)
            print(f"POST {sample['id']}: {response.status_code}")

        for _ in range(WAIT_SECONDS):
            results = []
            for sample in samples:
                response = await client.get(f"/tickets/{sample['id']}")
                results.append(response.json())

            pending = [r for r in results if r["status"] in ("pending", "processing")]
            if not pending:
                break
            await asyncio.sleep(1)

    print()
    for r in results:
        detail = r["summary"]
        if r["status"] != "classified":
            detail = r["last_error"]
        print(f"{r['id']}  {r['status']:<10} {r['category'] or '-':<9} {r['priority'] or '-':<6} {detail}")


if __name__ == "__main__":
    base_url = "http://localhost:8000"
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    asyncio.run(main(base_url))
