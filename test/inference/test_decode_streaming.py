"""
Tokenizer experiment: accumulate-and-diff (per-step delta) vs full decode.

Production `InferenceEngine.generate_stream` yields the full cumulative decoded
string each step (no `new_text[len(decoded_so_far):]` slicing), because ByteLevel
BPE can revise earlier boundaries and break UTF-8 when slicing by string length.

This file still tests the old diff helper for regression on tokenizer behavior.
"""
import pytest
from transformers import AutoTokenizer


def accumulate_diff_stream(tokenizer, token_ids_stream):
    """
    Replicate the accumulate-and-diff logic used in generate_stream.
    Yields (diff_text, running_text) pairs.
    """
    generated_ids = []
    decoded_so_far = ""
    for token_id in token_ids_stream:
        generated_ids.append(token_id)
        new_text = tokenizer.decode(
            generated_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        if new_text != decoded_so_far:
            yield new_text[len(decoded_so_far):], new_text
            decoded_so_far = new_text
    # flush
    if generated_ids:
        final = tokenizer.decode(
            generated_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        if final != decoded_so_far:
            yield final[len(decoded_so_far):], final


def test_chinese_character_streaming():
    """Chinese characters should be yielded one by one as they complete."""
    tokenizer = AutoTokenizer.from_pretrained(
        "Qwen/Qwen2.5-0.5B-Instruct",
        trust_remote_code=True,
    )

    # Manually simulate: "广 义 相 对 论 是 ..." (Chinese chars as individual tokens)
    # We need real token IDs for Chinese text
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "用一句话介绍广义相对论"},
    ]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    input_ids = tokenizer(text, return_tensors="pt").input_ids

    # Simulate tokens that form Chinese text
    # First, find the generation-start position
    prompt_len = len(input_ids[0])

    # Simulate a stream of token IDs (these would come from a model forward pass)
    # We'll use real tokenizer encode to get valid token sequences for test text
    sample_response = "广义相对论指出，重力是时空弯曲的表现。"
    response_tokens = tokenizer.encode(sample_response, add_special_tokens=False)

    chunks = []
    full_chunks = []
    for diff, full in accumulate_diff_stream(tokenizer, response_tokens):
        chunks.append(diff)
        full_chunks.append(full)

    assembled = "".join(chunks)
    assert assembled == sample_response, f"Expected {sample_response!r}, got {assembled!r}"
    print(f"Chunks: {chunks}")
    print(f"Full at each step: {full_chunks}")


def test_accumulate_diff_matches_final_decode():
    """The sum of diffs must equal tokenizer.decode(all_ids)."""
    tokenizer = AutoTokenizer.from_pretrained(
        "Qwen/Qwen2.5-0.5B-Instruct",
        trust_remote_code=True,
    )

    sample_response = "重力是时空的弯曲。Energy and matter curve spacetime."
    response_tokens = tokenizer.encode(sample_response, add_special_tokens=False)

    chunks = []
    for diff, _ in accumulate_diff_stream(tokenizer, response_tokens):
        chunks.append(diff)

    assembled = "".join(chunks)
    final = tokenizer.decode(response_tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False)
    assert assembled == final, f"assembled={assembled!r}, final={final!r}"


def test_mixed_language_streaming():
    """English + Chinese + punctuation mixed stream."""
    tokenizer = AutoTokenizer.from_pretrained(
        "Qwen/Qwen2.5-0.5B-Instruct",
        trust_remote_code=True,
    )

    sample_response = "E=mc² 说明能量与质量可以相互转换。This is a test. 123"
    response_tokens = tokenizer.encode(sample_response, add_special_tokens=False)

    chunks = []
    for diff, _ in accumulate_diff_stream(tokenizer, response_tokens):
        chunks.append(diff)

    assembled = "".join(chunks)
    final = tokenizer.decode(response_tokens, skip_special_tokens=True, clean_up_tokenization_spaces=False)
    assert assembled == final, f"assembled={assembled!r}, final={final!r}"


def test_special_tokens_are_skipped():
    """Special tokens (pad, eos, etc.) must not appear in output."""
    tokenizer = AutoTokenizer.from_pretrained(
        "Qwen/Qwen2.5-0.5B-Instruct",
        trust_remote_code=True,
    )

    sample = "Hello world"
    tokens = tokenizer.encode(sample, add_special_tokens=False)

    # Prepend/append only if they are not None
    all_tokens = []
    if tokenizer.bos_token_id is not None:
        all_tokens.append(tokenizer.bos_token_id)
    all_tokens.extend(tokens)
    if tokenizer.eos_token_id is not None:
        all_tokens.append(tokenizer.eos_token_id)

    chunks = []
    for diff, full in accumulate_diff_stream(tokenizer, all_tokens):
        chunks.append(diff)

    assembled = "".join(chunks)
    if tokenizer.bos_token_id is not None:
        assert tokenizer.bos_token not in assembled, f"BOS in assembled: {assembled!r}"
    if tokenizer.eos_token_id is not None:
        assert tokenizer.eos_token not in assembled, f"EOS in assembled: {assembled!r}"
    assert assembled == sample, f"Expected {sample!r}, got {assembled!r}"


def test_whitespace_preserved():
    """Spaces and newlines must be preserved correctly."""
    tokenizer = AutoTokenizer.from_pretrained(
        "Qwen/Qwen2.5-0.5B-Instruct",
        trust_remote_code=True,
    )

    sample = "Hello\nWorld\n\nThis is  spaced."
    response_tokens = tokenizer.encode(sample, add_special_tokens=False)

    chunks = []
    for diff, _ in accumulate_diff_stream(tokenizer, response_tokens):
        chunks.append(diff)

    assembled = "".join(chunks)
    assert assembled == sample, f"Expected {sample!r}, got {assembled!r}"


def test_no_empty_chunks_unless_stable():
    """Only yield when decoded text actually grows."""
    tokenizer = AutoTokenizer.from_pretrained(
        "Qwen/Qwen2.5-0.5B-Instruct",
        trust_remote_code=True,
    )

    sample = "你好"
    tokens = tokenizer.encode(sample, add_special_tokens=False)

    yielded = []
    for diff, full in accumulate_diff_stream(tokenizer, tokens):
        yielded.append((diff, full))

    # Every yielded diff must be non-empty
    for diff, _ in yielded:
        assert len(diff) > 0, f"Empty diff yielded when full={full!r}"


if __name__ == "__main__":
    test_chinese_character_streaming()
    test_accumulate_diff_matches_final_decode()
    test_mixed_language_streaming()
    test_special_tokens_are_skipped()
    test_whitespace_preserved()
    test_no_empty_chunks_unless_stable()
    print("All tests passed.")
