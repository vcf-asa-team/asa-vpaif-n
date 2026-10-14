#!/usr/bin/env python3
"""Generate test traffic against an OpenAI-compatible endpoint (e.g. vLLM's
`/v1` server) so the Grafana dashboard in ../grafana has something to show.

Examples:

    # 50 sequential requests
    python generate_traffic.py --base-url http://localhost:8000/v1 \\
        --model gemma-4-26b-it --num-requests 50

    # 200 requests, 10 at a time
    python generate_traffic.py --base-url http://localhost:8000/v1 \\
        --model gemma-4-26b-it --num-requests 200 --concurrency 10

    # Keep hammering it for 5 minutes with 20 concurrent workers
    python generate_traffic.py --base-url http://localhost:8000/v1 \\
        --model gemma-4-26b-it --duration 300 --concurrency 20 --stream
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from openai import AsyncOpenAI

DEFAULT_PROMPTS = [
    "Explain the theory of relativity in simple terms.",
    "Write a short poem about autumn leaves.",
    "What are the main differences between TCP and UDP?",
    "Summarize the plot of Romeo and Juliet in two sentences.",
    "Give me a recipe for a quick weeknight dinner.",
    "What causes the aurora borealis?",
    "List five tips for improving sleep quality.",
    "Explain how a binary search algorithm works.",
    "Describe the water cycle in a few sentences.",
    "What is the difference between machine learning and deep learning?",
    "Write a one-paragraph product description for a reusable water bottle.",
    "Translate 'good morning, how are you?' into French, Spanish, and Japanese.",
    "What are three common causes of a memory leak in a long-running service?",
    "Draft a polite email declining a meeting invite.",
    "Explain what a KV cache is in the context of transformer inference.",
]


@dataclass
class RequestResult:
    ok: bool
    latency_s: float
    ttft_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None


def load_prompts(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_PROMPTS
    lines = [line.strip() for line in Path(path).read_text().splitlines()]
    prompts = [line for line in lines if line]
    if not prompts:
        raise ValueError(f"No prompts found in {path}")
    return prompts


async def send_request(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    stream: bool,
) -> RequestResult:
    start = time.perf_counter()
    try:
        if stream:
            ttft = None
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            async for chunk in response:
                if ttft is None and chunk.choices and chunk.choices[0].delta.content:
                    ttft = time.perf_counter() - start
            latency = time.perf_counter() - start
            return RequestResult(ok=True, latency_s=latency, ttft_s=ttft)

        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        latency = time.perf_counter() - start
        usage = response.usage
        return RequestResult(
            ok=True,
            latency_s=latency,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )
    except Exception as exc:  # noqa: BLE001 - report any failure (timeout, APIError, etc.), keep going
        latency = time.perf_counter() - start
        return RequestResult(ok=False, latency_s=latency, error=str(exc))


async def worker(
    name: int,
    client: AsyncOpenAI,
    model: str,
    prompts: list[str],
    max_tokens: int,
    temperature: float,
    stream: bool,
    queue: "asyncio.Queue[int] | None",
    stop_event: asyncio.Event | None,
    results: list[RequestResult],
    verbose: bool,
) -> None:
    while True:
        if queue is not None:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
        elif stop_event is not None and stop_event.is_set():
            return

        prompt = random.choice(prompts)
        result = await send_request(client, model, prompt, max_tokens, temperature, stream)
        results.append(result)

        if verbose:
            status = "ok" if result.ok else f"ERROR: {result.error}"
            print(f"[worker {name}] #{len(results)} {result.latency_s:.2f}s {status}", flush=True)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] * (c - k) + ordered[c] * (k - f)


def print_summary(results: list[RequestResult], elapsed: float) -> None:
    total = len(results)
    ok_results = [r for r in results if r.ok]
    err_results = [r for r in results if not r.ok]
    latencies = [r.latency_s for r in ok_results]
    ttfts = [r.ttft_s for r in ok_results if r.ttft_s is not None]
    prompt_tokens = sum(r.prompt_tokens or 0 for r in ok_results)
    completion_tokens = sum(r.completion_tokens or 0 for r in ok_results)

    print("\n--- Summary ---")
    print(f"Total requests : {total}")
    print(f"Succeeded      : {len(ok_results)}")
    print(f"Failed         : {len(err_results)}")
    print(f"Elapsed        : {elapsed:.1f}s")
    print(f"Throughput     : {total / elapsed:.2f} req/s" if elapsed > 0 else "Throughput     : n/a")

    if latencies:
        print(
            "Latency (s)    : "
            f"p50={percentile(latencies, 0.50):.2f} "
            f"p95={percentile(latencies, 0.95):.2f} "
            f"p99={percentile(latencies, 0.99):.2f}"
        )
    if ttfts:
        print(
            "TTFT (s)       : "
            f"p50={percentile(ttfts, 0.50):.2f} "
            f"p95={percentile(ttfts, 0.95):.2f} "
            f"p99={percentile(ttfts, 0.99):.2f}"
        )
    if prompt_tokens or completion_tokens:
        print(f"Tokens         : prompt={prompt_tokens} generation={completion_tokens}")

    if err_results:
        print("\nSample errors:")
        seen = set()
        for r in err_results:
            if r.error not in seen:
                print(f"  - {r.error}")
                seen.add(r.error)
            if len(seen) >= 5:
                break


async def main_async(args: argparse.Namespace) -> None:
    prompts = load_prompts(args.prompts_file)
    http_client = httpx.AsyncClient(verify=not args.insecure)
    client = AsyncOpenAI(base_url=args.base_url, api_key=args.api_key, http_client=http_client)

    results: list[RequestResult] = []
    queue: "asyncio.Queue[int] | None" = None
    stop_event: asyncio.Event | None = None

    if args.duration is not None:
        stop_event = asyncio.Event()
    else:
        queue = asyncio.Queue()
        for _ in range(args.num_requests):
            queue.put_nowait(1)

    workers = [
        asyncio.create_task(
            worker(
                i,
                client,
                args.model,
                prompts,
                args.max_tokens,
                args.temperature,
                args.stream,
                queue,
                stop_event,
                results,
                args.verbose,
            )
        )
        for i in range(args.concurrency)
    ]

    start = time.perf_counter()
    try:
        if stop_event is not None:
            await asyncio.sleep(args.duration)
            stop_event.set()
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        if stop_event is not None:
            stop_event.set()
        raise
    finally:
        elapsed = time.perf_counter() - start
        await client.close()

    print_summary(results, elapsed)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="OpenAI-compatible base URL, e.g. http://localhost:8000/v1")
    parser.add_argument("--api-key", default="EMPTY", help="API key (vLLM ignores it by default; pass any string)")
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification (self-signed certs)")
    parser.add_argument("--model", required=True, help="Model name as registered with the server (model_name label)")
    parser.add_argument("--concurrency", type=int, default=1, help="Number of concurrent workers (default: 1)")
    parser.add_argument("--num-requests", type=int, default=100, help="Total requests to send (ignored if --duration is set)")
    parser.add_argument("--duration", type=float, default=None, help="Run for this many seconds instead of a fixed request count")
    parser.add_argument("--max-tokens", type=int, default=128, help="max_tokens per request (default: 128)")
    parser.add_argument("--temperature", type=float, default=0.7, help="Sampling temperature (default: 0.7)")
    parser.add_argument("--stream", action="store_true", help="Use streaming responses and measure time-to-first-token")
    parser.add_argument("--prompts-file", default=None, help="Path to a newline-delimited file of prompts (default: built-in sample prompts)")
    parser.add_argument("--verbose", action="store_true", help="Print a line per completed request")
    args = parser.parse_args(argv)

    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")
    if args.duration is None and args.num_requests < 1:
        parser.error("--num-requests must be >= 1")
    return args


def main() -> None:
    args = parse_args(sys.argv[1:])
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
