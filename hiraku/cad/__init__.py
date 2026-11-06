"""CAD in and out of HIRAKU through STEP (ISO 10303-21).

A package on its own: it imports only the standard library, numpy and hiraku.contract, and nothing
outside it imports it except the tool adapter hiraku/tools/cad_tools.py. The only data it shares
with the rest of HIRAKU is the contract's design record.

    step_write.design_to_step(design) -> STEP text (one solid per member and per part)
    step_read.read_step(text) -> [Solid]          (names, boxes, volumes, centres)
    import_design.step_to_parts / step_to_frame / design_from_step
    step_edit.edit_step(text, edits) -> STEP text (read, change, write again)
"""


class CadError(ValueError):
    """A STEP file or a design that this package cannot handle; the message names the entity or shape."""
