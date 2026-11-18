---
description: The daemon filters phonemizer's "words count mismatch" warning out of its log, rather than lowering the phonemizer logger's level, so its other warnings and errors still show.
status: accepted
---

# ADR-0034: Phonemizer's word count warning

## Context

Kokoro turns text into phonemes with phonemizer's espeak backend. After each call, phonemizer checks that its output has as many words as its input and logs `WARNING phonemizer: words count mismatch on 100.0% of the lines (1/1)` when it doesn't.

espeak reads numbers, commit hashes and similar out as several words: "e942277" becomes "e nine hundred forty two thousand two hundred seventy seven". So most sentences that mention a commit, a count or a PR number log the warning. The acceptance run (ADR-0025) saw it on nearly every sentence, in the daemon log that `doctor` and the README point people to.

The check only matters to a caller that splits the output back into words to line them up with the input. Kokoro doesn't: it joins the phonemes into one string. The cause is in how espeak reads text, and the call is inside kokoro-onnx, so there's nothing in this project's code to fix. Writing numbers out before synthesis would change what gets spoken, which is wording's job, not speech's.

## Decision

- **`speech.py` adds a filter to the `phonemizer` logger** that drops records whose message starts with `words count mismatch`. phonemizer logs the warning on that logger itself, so the filter sees every one.
- **The logger's level stays as it is.** Lowering it to `ERROR` would also hide phonemizer's other warnings, such as language switches, which do say something went wrong with how a sentence will sound.

## Consequences

- A spoken sentence no longer logs the warning. phonemizer's other warnings and errors, and the daemon's own speech warnings, such as an unknown voice or `afplay` failing, are logged as before.
- The filter matches phonemizer's message text. If a later phonemizer rewords it, the warning comes back, which is noisy but harmless; a test that phonemizes a sentence with numbers catches it.
