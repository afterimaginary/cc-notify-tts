"""The wording eval: how well local models write a notification's detail.

`cases` loads the hand-written hook payloads, `score` checks one answer against
its case, `run` asks Ollama and writes the results, and `report` and `compare`
turn results into Markdown. How to run it: evals/README.md.
"""
