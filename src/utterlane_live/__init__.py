"""Utterlane Live: hands-free, multi-speaker Turkish voice conversation.

Everything except the language model runs on the local machine: VAD, speech-to-text,
speaker embeddings, speaker naming, voice profiles and text-to-speech. The language
model (``gpt-6-luna``) only receives conversation text with speaker labels.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
