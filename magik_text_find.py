"""Magik Text Find - search comma-separated targets in a text, with word-index windowing."""

import re

from typing_extensions import override
from comfy_api.latest import ComfyExtension, io

# A "word" is a run of letters/digits/underscores, optionally joined by inner
# hyphens or apostrophes (so "well-known", "don't", "long_hair" stay whole and
# surrounding punctuation such as "dog," or "(dog)" is ignored).
_WORD_RE = re.compile(r"\w+(?:[-'\u2019]\w+)*")

# Custom socket type: the indices are handed over as ONE Python list[int].
IntList = io.Custom("INT_LIST")


def target_matches(spans, target, case_sensitive):
    """Return (start_word, end_word) matches, longest first at each start.

    Commas separate targets. Empty entries and duplicate token sequences are
    ignored. Search includes overlapping matches; replacement uses the longest
    available match at each position and skips already replaced words.
    """
    norm = (lambda value: value) if case_sensitive else (lambda value: value.casefold())
    targets = set()
    for entry in (target or "").split(","):
        tokens = tuple(norm(word) for word in _WORD_RE.findall(entry))
        if tokens:
            targets.add(tokens)
    by_first = {}
    for tokens in targets:
        by_first.setdefault(tokens[0], []).append(tokens)
    for candidates in by_first.values():
        candidates.sort(key=len, reverse=True)
    words = [norm(span.group()) for span in spans]
    matches = []
    for i, word in enumerate(words):
        for tokens in by_first.get(word, ()):
            end = i + len(tokens)
            if tuple(words[i:end]) == tokens:
                matches.append((i, end))
    return matches


def find_in_text(text, target, first_idx, last_idx, from_start, case_sensitive):
    """Pure search logic (no ComfyUI dependency).

    Returns (found, count, indices, processed_text).
    `indices` are 0-based word positions in the ORIGINAL text, ascending,
    regardless of `from_start`.
    """
    text = text or ""
    target = target or ""

    spans = list(_WORD_RE.finditer(text))
    n = len(spans)

    # Kept words are those with index in [first_idx, last_idx] (inclusive);
    # last_idx == 0 means "up to the end". Past-the-end values are clamped.
    # Result is a window [lo, hi) in original-text word coordinates.
    end = n if last_idx == 0 else min(last_idx + 1, n)  # exclusive bound
    if from_start:
        lo, hi = first_idx, end
    else:
        # Backwards processing: indices count from the end of the text, so
        # convert the reversed range [first_idx, end) to original coordinates.
        lo, hi = n - end, n - first_idx

    if n == 0:
        return False, 0, [], text
    if lo >= hi:
        return False, 0, [], ""

    window = spans[lo:hi]
    start = 0 if lo == 0 else window[0].start()
    end = len(text) if hi >= n else window[-1].end()
    processed = text[start:end]

    matches = target_matches(window, target, case_sensitive)
    indices = [lo + start for start, _ in matches]
    return len(indices) > 0, len(indices), indices, processed


def replace_in_processed_text(processed, target, new, case_sensitive, only_first=False):
    """Replace non-overlapping matches using the same word rules as search."""
    spans = list(_WORD_RE.finditer(processed))
    matches = target_matches(spans, target, case_sensitive)
    pieces = []
    cursor = 0
    for first, end_word in matches:
        start, end = spans[first].start(), spans[end_word - 1].end()
        if start < cursor:
            continue
        replacement = (new or "").replace("{T}", processed[start:end])
        pieces.extend((processed[cursor:start], replacement))
        cursor = end
        if only_first:
            break
    pieces.append(processed[cursor:])
    return "".join(pieces)


class MagikTextFind(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Magik.TextFind",
            display_name="Magik Text Find",
            category="Magik/Tool/Text",
            is_input_list=True,
            description="Finds whole-word occurrences of Target in Text, "
                        "optionally restricted to a window of words.",
            inputs=[
                io.String.Input(
                    "text", display_name="Text", force_input=True,
                    tooltip="Text to search in.",
                ),
                io.String.Input(
                    "target", display_name="Target", default="",
                    tooltip="Comma-separated words or multiword tags, e.g. bob, lady di. Empty and duplicate targets are ignored; punctuation is ignored.",
                ),
                io.Int.Input(
                    "first_word_index", display_name="First word index",
                    default=0, min=0, max=9999,
                    tooltip="Index of the first word processed (0-based); earlier words "
                            "are skipped. Counts from the end if From Start is off.",
                ),
                io.Int.Input(
                    "last_word_index", display_name="Last word index",
                    default=0, min=0, max=9999,
                    tooltip="Index of the last word processed (inclusive); words after "
                            "it are skipped. 0 = process up to the end. Counts from "
                            "the end if From Start is off.",
                ),
                io.Boolean.Input(
                    "from_start", display_name="From Start", default=True,
                    tooltip="On: normal. Off: text is processed backwards, so both "
                            "word indices count from the end of the text.",
                ),
                io.Boolean.Input(
                    "case_sensitive", display_name="Case Sensitive", default=False,
                    tooltip="Off: 'Cat' matches 'cat'.",
                ),
                io.Boolean.Input(
                    "replace", display_name="Replace", default=False,
                    tooltip="Replace matches in Processed Text; overlapping matches use the longest match at the earliest position.",
                ),
                io.Boolean.Input(
                    "only_first", display_name="Only 1st", default=False,
                    tooltip="Replace only the earliest match across all targets. "
                            "Count and Indices still include every match.",
                ),
                io.String.Input(
                    "new", display_name="New", default="",
                    tooltip="Replacement text. {T} inserts the matched text with its original casing. Empty text removes matches.",
                ),
                io.Int.Input(
                    "quadrants", display_name="Quadrants", optional=True,
                    force_input=True,
                    tooltip="Connect quadrants from Magik Where Is The Face. "
                            "1-based: 1 selects the first string, 2 the second, "
                            "3 the third, 4 the fourth. Unconnected: replace in all "
                            "strings. Empty list: replace none. Search is unaffected.",
                ),
            ],
            outputs=[
                io.Boolean.Output("found", is_output_list=True, display_name="found",
                                  tooltip="True if Target is in the processed text."),
                io.Int.Output("count", is_output_list=True, display_name="count",
                              tooltip="Total occurrences across all targets, including overlapping matches."),
                IntList.Output("indices", is_output_list=True, display_name="indices",
                               tooltip="0-based word positions of Target in the "
                                       "original text (single INT_LIST value)."),
                io.String.Output("processed_text", is_output_list=True, display_name="Processed Text",
                                 tooltip="The part of Text that was actually searched."),
                io.String.Output("new_text", is_output_list=True, display_name="New Text",
                                 tooltip="Replaced Processed Text when Replace is on; "
                                         "otherwise the original input Text unchanged."),
            ],
        )

    @classmethod
    def execute(cls, text, target, first_word_index, last_word_index,
                from_start, case_sensitive, replace=False, new="", only_first=False,
                quadrants=None):
        # INPUT_IS_LIST gives access to the whole STRING execution list instead
        # of calling this node separately for each string.
        texts = text if isinstance(text, (list, tuple)) else [text]
        selected = None if quadrants is None else {
            q - 1 for q in quadrants
            if isinstance(q, int) and not isinstance(q, bool) and 1 <= q <= 4
        }

        def setting(value, index, default):
            # Match ComfyUI's usual broadcasting: repeat the final value when
            # another input list is shorter than Text.
            if isinstance(value, (list, tuple)):
                return value[min(index, len(value) - 1)] if value else default
            return value

        outputs = [[] for _ in range(5)]
        for index, item in enumerate(texts):
            item_target = setting(target, index, "")
            sensitive = setting(case_sensitive, index, False)
            found, count, indices, processed = find_in_text(
                item, item_target,
                setting(first_word_index, index, 0),
                setting(last_word_index, index, 0),
                setting(from_start, index, True), sensitive,
            )
            should_replace = setting(replace, index, False) and (
                selected is None or index in selected
            )
            new_text = (
                replace_in_processed_text(
                    processed, item_target, setting(new, index, ""), sensitive,
                    setting(only_first, index, False),
                ) if should_replace else item
            )
            for output, value in zip(outputs, (found, count, indices, processed, new_text)):
                output.append(value)
        return io.NodeOutput(*outputs)


class MagikTextFindExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [MagikTextFind]


async def comfy_entrypoint() -> MagikTextFindExtension:
    return MagikTextFindExtension()