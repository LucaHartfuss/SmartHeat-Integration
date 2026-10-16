"""Pin gegen den Einrichtungs-Vertrag (Spec Stufe 3 S5 4): Ein Modul, das Felder einer Antwort als Literale liest,
pinnt sie hier beim Import gegen die Konstanten des Vertrags (`const.EINRICHTUNG_*_FELDER`); liest es ein Feld, das der
Vertrag nicht nennt, ist das ein RuntimeError beim Import statt ein stilles None im Betrieb (kein assert: das
verschwindet unter -O). Rein, ohne Home-Assistant-Import; gleiche Form wie geraete/vertrag_pin.py des Servers."""
from collections.abc import Mapping, Sequence


def enthalten(modul: str, pins: Mapping[str, tuple[Sequence, Sequence]]) -> None:
    """Je Name (Vertragswert, gelesene Literale): jedes gelesene Literal muss im Vertrag stehen."""
    for name, (vertrag, literal) in pins.items():
        fremd = sorted(set(literal) - set(vertrag))
        if fremd:
            raise RuntimeError(f"{modul} liest aus {name} {fremd}, der Vertrag nennt {tuple(vertrag)}")
