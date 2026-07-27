from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.errors import ApiError


_USD_TO_CNY = 6.8
_PUMP_ITEM_ID = "milk-pump"
_BUDGET_REMOVE_ORDER = (
    "baby-clothes",
    "mom-briefs",
    "milk-bra",
    "milk-storage",
    "baby-bath-towel",
    "mom-bottle",
    "milk-bottle",
    "milk-cream",
    "mom-wipes",
    "milk-pad",
    "baby-towel",
)
_PROTECTED_ITEM_IDS = {
    "mom-pad",
    "mom-sanitary",
    "mom-underwear",
    "baby-diaper",
    "baby-wipes",
    "baby-blanket",
    "baby-blanket-basic",
    _PUMP_ITEM_ID,
}
_BASIC_REPLACEMENTS: dict[str, dict[str, Any]] = {
    "baby-blanket": {
        "id": "baby-blanket-basic",
        "name": "基础款宝宝包被",
        "desc": "先选基础款，按季节再加外层",
        "qty": 1,
        "price": 59.9,
        "keywords": ["包被"],
    }
}
_ORIGINAL_ITEM_IDS = {
    replacement["id"]: original_id
    for original_id, replacement in _BASIC_REPLACEMENTS.items()
}

DEFAULT_HOSPITAL_BAG_CART_GROUPS: list[dict[str, Any]] = [
    {
        "title": "妈妈护理",
        "tone": "rose",
        "items": [
            {
                "id": "mom-pad",
                "name": "产褥垫组合装",
                "desc": "入院与产后前几天使用",
                "qty": 1,
                "price": 59.9,
                "keywords": ["产褥垫", "护理垫"],
            },
            {
                "id": "mom-sanitary",
                "name": "产妇卫生巾",
                "desc": "夜用加长款，按住院天数准备",
                "qty": 1,
                "price": 39.9,
                "keywords": ["卫生巾"],
            },
            {
                "id": "mom-underwear",
                "name": "一次性内裤",
                "desc": "高腰柔软，产后更方便更换",
                "qty": 1,
                "price": 49.9,
                "keywords": ["内裤", "一次性内裤"],
            },
            {
                "id": "mom-wipes",
                "name": "产后护理湿巾",
                "desc": "温和清洁，适合住院随身包",
                "qty": 1,
                "price": 29.9,
                "keywords": ["湿巾", "护理湿巾"],
            },
            {
                "id": "mom-bottle",
                "name": "产后冲洗瓶",
                "desc": "产后清洁更方便",
                "qty": 1,
                "price": 39.9,
                "keywords": ["冲洗瓶"],
            },
            {
                "id": "mom-briefs",
                "name": "高腰收腹内裤",
                "desc": "不压腹，更适合产后恢复期穿着",
                "qty": 1,
                "price": 69.9,
                "keywords": ["收腹", "高腰"],
            },
        ],
    },
    {
        "title": "宝宝出院",
        "tone": "mint",
        "items": [
            {
                "id": "baby-diaper",
                "name": "新生儿纸尿裤",
                "desc": "NB 码小包装",
                "qty": 1,
                "price": 59.9,
                "keywords": ["纸尿裤", "尿不湿"],
            },
            {
                "id": "baby-wipes",
                "name": "婴儿柔湿巾",
                "desc": "无香精",
                "qty": 1,
                "price": 29.9,
                "keywords": ["婴儿湿巾", "柔湿巾"],
            },
            {
                "id": "baby-towel",
                "name": "棉柔巾",
                "desc": "洗脸、擦手和护理使用",
                "qty": 1,
                "price": 29.9,
                "keywords": ["棉柔巾"],
            },
            {
                "id": "baby-blanket",
                "name": "宝宝出院包被",
                "desc": "按季节搭配外层",
                "qty": 1,
                "price": 129.0,
                "keywords": ["包被"],
            },
            {
                "id": "baby-clothes",
                "name": "新生儿连体衣礼盒",
                "desc": "出院和回家第一周替换穿",
                "qty": 1,
                "price": 159.0,
                "keywords": ["连体衣", "衣服"],
            },
            {
                "id": "baby-bath-towel",
                "name": "婴儿浴巾",
                "desc": "洗澡、包裹和保暖使用",
                "qty": 1,
                "price": 59.9,
                "keywords": ["浴巾"],
            },
        ],
    },
    {
        "title": "母乳喂养",
        "tone": "sky",
        "items": [
            {
                "id": "milk-pad",
                "name": "防溢乳垫",
                "desc": "母乳或混合喂养可先备小包装",
                "qty": 1,
                "price": 39.9,
                "keywords": ["防溢乳垫", "乳垫"],
            },
            {
                "id": "milk-cream",
                "name": "乳头护理霜",
                "desc": "哺乳初期不适时可咨询后使用",
                "qty": 1,
                "price": 49.9,
                "keywords": ["乳头霜", "护理霜"],
            },
            {
                "id": "milk-storage",
                "name": "储奶袋",
                "desc": "返家后储奶备用",
                "qty": 1,
                "price": 49.9,
                "keywords": ["储奶袋"],
            },
            {
                "id": _PUMP_ITEM_ID,
                "name": "便携式吸奶器",
                "desc": "是否带去医院先问医院",
                "qty": 1,
                "price": 699.0,
                "keywords": ["吸奶器"],
            },
            {
                "id": "milk-bra",
                "name": "哺乳文胸",
                "desc": "产后和哺乳初期穿着",
                "qty": 1,
                "price": 159.0,
                "keywords": ["哺乳文胸", "文胸"],
            },
            {
                "id": "milk-bottle",
                "name": "宽口径奶瓶",
                "desc": "混合喂养或返家后备用",
                "qty": 1,
                "price": 89.9,
                "keywords": ["奶瓶"],
            },
        ],
    },
]


def reduce_hospital_bag_cart(
    *,
    arguments: dict[str, Any],
    runtime_cart: object,
    pump_products: list[dict[str, Any]],
) -> dict[str, Any]:
    operation = str(arguments["operation"])
    groups = _current_groups(runtime_cart)
    before_totals = _totals(groups)
    removed_ids: list[str] = []
    restored_ids: list[str] = []
    replaced_items: list[dict[str, Any]] = []

    if operation == "set_pump_model":
        groups, replaced_items = _set_pump(
            groups,
            sku_id=str(arguments["product_sku_id"]),
            pump_products=pump_products,
        )
    elif operation in {"remove_items", "mark_provided", "mark_owned"}:
        removed_ids = list(arguments["item_ids"])
        groups = _remove(groups, set(removed_ids))
    elif operation == "restore_items":
        restored_ids = list(arguments["item_ids"])
        groups = _restore(
            groups,
            item_ids=restored_ids,
            pump_products=pump_products,
        )
    elif operation == "replace_items":
        groups, replaced_items = _replace(
            groups,
            set(arguments["item_ids"]),
        )
    elif operation == "update_quantity":
        groups, removed_ids = _update_quantities(
            groups,
            updates=list(arguments["quantity_updates"]),
        )
    elif operation == "optimize_budget":
        groups, removed_ids, replaced_items = _optimize_budget(
            groups,
            arguments=arguments,
        )
    elif operation == "reset_cart":
        groups = deepcopy(DEFAULT_HOSPITAL_BAG_CART_GROUPS)
    else:  # pragma: no cover - Pydantic/schema validation owns this branch.
        raise _invalid_cart("unsupported_cart_operation")

    totals = _totals(groups)
    target_budget = arguments.get("target_budget")
    cart_update: dict[str, Any] = {
        "action": operation,
        "groups": groups,
        "totals": totals,
        "before_totals": before_totals,
        "removed_item_ids": removed_ids,
        "restored_item_ids": restored_ids,
        "replaced_items": replaced_items,
        "message": _summary(operation, totals),
    }
    if isinstance(target_budget, (int, float)):
        cart_update["target_budget"] = float(target_budget)
        cart_update["budget_met"] = (
            totals["total"] <= float(target_budget)
        )
    return cart_update


def _current_groups(runtime_cart: object) -> list[dict[str, Any]]:
    if not isinstance(runtime_cart, dict):
        return deepcopy(DEFAULT_HOSPITAL_BAG_CART_GROUPS)
    groups = runtime_cart.get("groups")
    if not isinstance(groups, list):
        raise _invalid_cart("runtime_cart_groups_invalid")
    return [
        deepcopy(group)
        for group in groups
        if isinstance(group, dict)
        and isinstance(group.get("items"), list)
    ]


def _set_pump(
    groups: list[dict[str, Any]],
    *,
    sku_id: str,
    pump_products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    product = next(
        (
            value
            for value in pump_products
            if str(value.get("sku_id") or "") == sku_id
        ),
        None,
    )
    if product is None:
        raise _invalid_cart("pump_sku_not_found")
    price_usd = float(product.get("sale_price") or product["official_price"])
    item = {
        "id": sku_id,
        "sku_id": sku_id,
        "model": str(product["model"]),
        "name": str(product["name"]),
        "desc": str(product.get("best_for") or ""),
        "qty": 1,
        "price": round(price_usd * _USD_TO_CNY, 2),
        "currency": "CNY",
        "official_price_usd": float(product["official_price"]),
        "sale_price_usd": product.get("sale_price"),
        "exchange_rate_usd_cny": _USD_TO_CNY,
        "product_url": str(product["source_url"]),
        "image_url": str(product["image_url"]),
        "image_alt": str(product["name"]),
        "keywords": ["吸奶器", str(product["model"]), sku_id],
    }
    next_groups = deepcopy(groups)
    pump_ids = {
        _PUMP_ITEM_ID,
        *(
            str(value.get("sku_id") or "")
            for value in pump_products
        ),
    }
    for group in next_groups:
        items = group["items"]
        for index, current in enumerate(items):
            if not isinstance(current, dict):
                continue
            current_id = str(current.get("id") or "")
            if (
                current_id in pump_ids
                or "吸奶器" in str(current.get("name") or "")
            ):
                items[index] = item
                if current_id == sku_id:
                    return next_groups, []
                return next_groups, [
                    {
                        "from_item_id": current_id,
                        "from_name": str(current.get("name") or ""),
                        "to_item_id": sku_id,
                        "to_name": item["name"],
                    }
                ]
    target = next(
        (
            group
            for group in next_groups
            if group.get("title") == "母乳喂养"
        ),
        None,
    )
    if target is None:
        target = {"title": "母乳喂养", "tone": "sky", "items": []}
        next_groups.append(target)
    target["items"].append(item)
    return next_groups, []


def _remove(
    groups: list[dict[str, Any]],
    item_ids: set[str],
) -> list[dict[str, Any]]:
    return [
        {
            **deepcopy(group),
            "items": [
                deepcopy(item)
                for item in group["items"]
                if isinstance(item, dict)
                and str(item.get("id") or "") not in item_ids
            ],
        }
        for group in groups
    ]


def _restore(
    groups: list[dict[str, Any]],
    *,
    item_ids: list[str],
    pump_products: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    next_groups = deepcopy(groups)
    existing = _item_ids(next_groups)
    defaults = _default_items(pump_products)
    for item_id in item_ids:
        if item_id in existing:
            continue
        original_id = _ORIGINAL_ITEM_IDS.get(item_id, item_id)
        default = defaults.get(original_id)
        if default is None:
            raise _invalid_cart("restore_item_not_found")
        title, tone, item = default
        target = next(
            (
                group
                for group in next_groups
                if group.get("title") == title
            ),
            None,
        )
        if target is None:
            target = {"title": title, "tone": tone, "items": []}
            next_groups.append(target)
        target["items"].append(deepcopy(item))
        existing.add(str(item["id"]))
    return next_groups


def _replace(
    groups: list[dict[str, Any]],
    item_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    next_groups = deepcopy(groups)
    replaced: list[dict[str, Any]] = []
    for group in next_groups:
        for index, item in enumerate(group["items"]):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            replacement = _BASIC_REPLACEMENTS.get(item_id)
            if item_id in item_ids and replacement is not None:
                group["items"][index] = deepcopy(replacement)
                replaced.append(
                    {
                        "from_item_id": item_id,
                        "from_name": str(item.get("name") or ""),
                        "to_item_id": str(replacement["id"]),
                        "to_name": str(replacement["name"]),
                    }
                )
    if len(replaced) != len(item_ids):
        raise _invalid_cart("replacement_not_available")
    return next_groups, replaced


def _update_quantities(
    groups: list[dict[str, Any]],
    *,
    updates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    quantity_by_id = {
        str(update["item_id"]): int(update["qty"])
        for update in updates
    }
    next_groups = deepcopy(groups)
    removed: list[str] = []
    for group in next_groups:
        items: list[dict[str, Any]] = []
        for item in group["items"]:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            if item_id not in quantity_by_id:
                items.append(item)
                continue
            quantity = quantity_by_id[item_id]
            if quantity == 0:
                removed.append(item_id)
                continue
            item["qty"] = quantity
            items.append(item)
        group["items"] = items
    return next_groups, removed


def _optimize_budget(
    groups: list[dict[str, Any]],
    *,
    arguments: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    next_groups = deepcopy(groups)
    replaced: list[dict[str, Any]] = []
    if arguments.get("preference") != "comfort" and "baby-blanket" in _item_ids(
        next_groups
    ):
        next_groups, replaced = _replace(
            next_groups,
            {"baby-blanket"},
        )
    protected = set(_PROTECTED_ITEM_IDS)
    protected.update(arguments.get("preserve_item_ids") or [])
    pump_ids = {
        item_id
        for item_id in _item_ids(next_groups)
        if item_id == _PUMP_ITEM_ID or item_id.startswith("pump-")
    }
    if arguments.get("allow_remove_pump"):
        protected.difference_update(pump_ids)
    else:
        protected.update(pump_ids)
    target_budget = arguments.get("target_budget")
    budget_mode = arguments.get("budget_mode")
    candidates = list(_BUDGET_REMOVE_ORDER)
    if arguments.get("preference") == "breastfeeding":
        milk = {"milk-pad", "milk-cream", "milk-storage", "milk-bottle"}
        candidates = [value for value in candidates if value not in milk] + [
            value for value in candidates if value in milk
        ]
    if arguments.get("allow_remove_pump"):
        candidates.extend(sorted(pump_ids))
    removed: list[str] = []
    for item_id in candidates:
        if item_id in protected or item_id not in _item_ids(next_groups):
            continue
        if isinstance(target_budget, (int, float)):
            if _totals(next_groups)["total"] <= float(target_budget):
                break
        elif budget_mode == "cheaper" and len(removed) >= 3:
            break
        next_groups = _remove(next_groups, {item_id})
        removed.append(item_id)
    return next_groups, removed, replaced


def _default_items(
    pump_products: list[dict[str, Any]],
) -> dict[str, tuple[str, str, dict[str, Any]]]:
    result = {
        str(item["id"]): (
            str(group["title"]),
            str(group["tone"]),
            item,
        )
        for group in DEFAULT_HOSPITAL_BAG_CART_GROUPS
        for item in group["items"]
    }
    for product in pump_products:
        sku_id = str(product.get("sku_id") or "")
        if sku_id:
            pump_group, _ = _set_pump(
                [],
                sku_id=sku_id,
                pump_products=pump_products,
            )
            result[sku_id] = (
                "母乳喂养",
                "sky",
                pump_group[0]["items"][0],
            )
    return result


def _item_ids(groups: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("id") or "")
        for group in groups
        for item in group["items"]
        if isinstance(item, dict)
    }


def _totals(groups: list[dict[str, Any]]) -> dict[str, Any]:
    subtotal = 0.0
    item_count = 0
    for group in groups:
        for item in group["items"]:
            if not isinstance(item, dict):
                continue
            qty = max(1, int(item.get("qty") or 1))
            item_count += qty
            subtotal += float(item.get("price") or 0) * qty
    subtotal = round(subtotal, 2)
    discount = round(subtotal * 0.08, 2) if item_count else 0.0
    total = round(subtotal - discount, 2)
    return {
        "subtotal": subtotal,
        "item_count": item_count,
        "itemCount": item_count,
        "discount": discount,
        "shipping": 0.0,
        "total": total,
        "currency": "CNY",
        "mixed_currency": False,
    }


def _summary(operation: str, totals: dict[str, Any]) -> str:
    return (
        f"待产包购物车已执行 {operation}，"
        f"当前共 {totals['itemCount']} 件，预计合计 ¥{totals['total']:.2f}。"
    )


def _invalid_cart(reason: str) -> ApiError:
    return ApiError(
        code="invalid_hospital_bag_cart_mutation",
        message="The hospital bag cart mutation cannot be applied.",
        status=422,
        details={"reason": reason},
    )


__all__ = [
    "DEFAULT_HOSPITAL_BAG_CART_GROUPS",
    "reduce_hospital_bag_cart",
]
