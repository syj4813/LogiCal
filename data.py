# -*- coding: utf-8 -*-
"""공용 상수 모듈.

STATIONS는 rail_freight_nodes.FREIGHT_NODES에서 직접 import해서 만든다 —
좌표를 여기서 다시 정의하면 두 군데가 어긋날 수 있어서, 단일 출처만 둔다.
"""

from rail_freight_nodes import FREIGHT_NODES

STATIONS = {node.name: (node.lat, node.lng) for node in FREIGHT_NODES}

CARGO_TYPE_EXAMPLES = [
    "일반 화물(전자부품 등)",
    "냉동·냉장 식품",
    "위험물(화학품·배터리 등)",
    "파손주의·고가품",
    "농산물·수산물",
]
