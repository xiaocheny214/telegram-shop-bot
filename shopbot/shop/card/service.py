"""卡密导入、占用、释放、售出和作废。

先选定商品。商品有规格时必须再选定规格，没有规格时不能带规格。
批量内容按行读取，一行一条。写入在事务中执行。FastAPI 已开启事务时并入该事务。
同一批里重复或格式不对的行记为失败，成功的行和库存一起提交。

卡密就是可售库存。占用用条件更新，同一张卡密只能被一个订单拿走。
下单未支付就占用时调用 reserve_card，超时或取消调用 release_card，支付成功再调用 sell_card。
支付成功才占用时，sell_card 不传 card_id，直接从可用库存取一张标为已售。
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.db import transactional
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.enums.model import CardStatus
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.card.schemas import CardImport, CardImportFile, parse_card_id, parse_model
from shopbot.shop.models import Card, CardImportBatch, CardStock, Order, Product, ProductSpec
from shopbot.shop.product.service import get_product
from shopbot.system.audit.service import audit
from shopbot.system.vault import protect, reveal, show

_SECRET_MAX = 2_000
_LINE_MAX = 500
_STOCK_MAX = 1_000_000
_QUANTITY_MAX = 10_000
_FILE_MAX = 1_048_576
_TEXT_SUFFIXES = {".txt"}


@audit("card.import")
@transactional
async def import_cards(
    session: AsyncSession,
    payload: CardImport | dict,
) -> tuple[CardImportBatch, list[str]]:
    data = parse_model(CardImport, payload)
    product, spec = await _import_target(session, data.product_id, data.spec_id)
    accepted, failures, total = _split_lines(data.text)
    return await _store_import(session, product, spec, accepted, failures, total)


async def import_cards_from_file(
    session: AsyncSession,
    payload: CardImportFile | dict,
) -> tuple[CardImportBatch, list[str]]:
    data = parse_model(CardImportFile, payload)
    text = _read_text_file(data.path)
    return await import_cards(
        session,
        {"product_id": data.product_id, "spec_id": data.spec_id, "text": text},
    )


async def list_cards(
    session: AsyncSession,
    product_id: int,
    *,
    spec_id: int | None = None,
    status: str | None = None,
) -> list[Card]:
    if spec_id is None:
        product, _detail = await get_product(session, product_id)
        spec = None
    else:
        product, spec = await _import_target(session, product_id, spec_id)
    statement = select(Card).where(Card.product_id == product.id).order_by(Card.id.desc())
    if spec is not None:
        statement = statement.where(Card.spec_id == spec.id)
    if status is not None:
        if status not in CardStatus._value2member_map_:
            raise BizException("卡密状态无效", code=BizCode.BAD_REQUEST)
        statement = statement.where(Card.status == status)
    result = await session.scalars(statement.limit(200))
    cards = list(result.all())
    for card in cards:
        show(card, "secret", reveal(card.secret) or "")
    return cards


@dataclass(frozen=True)
class CardPage:
    items: list[Card]
    page: int
    page_size: int
    has_more: bool


async def page_cards(
    session: AsyncSession,
    product_id: int,
    spec_id: int | None,
    status: str,
    page: int,
    *,
    page_size: int = 5,
) -> CardPage:
    """按商品、规格和状态翻页。只解密这一页的卡密。"""
    if type(page) is not int or page < 1:
        raise BizException("页码无效", code=BizCode.BAD_REQUEST)
    if type(page_size) is not int or not 1 <= page_size <= 20:
        raise BizException("每页条数无效", code=BizCode.BAD_REQUEST)
    if status not in CardStatus._value2member_map_:
        raise BizException("卡密状态无效", code=BizCode.BAD_REQUEST)
    product, spec = await _import_target(session, product_id, spec_id)
    offset = (page - 1) * page_size
    statement = select(Card).where(Card.product_id == product.id, Card.status == status)
    if spec is None:
        statement = statement.where(Card.spec_id.is_(None))
    else:
        statement = statement.where(Card.spec_id == spec.id)
    rows = list(
        await session.scalars(statement.order_by(Card.id.desc()).offset(offset).limit(page_size + 1))
    )
    has_more = len(rows) > page_size
    items = rows[:page_size]
    for card in items:
        show(card, "secret", reveal(card.secret) or "")
    return CardPage(items=items, page=page, page_size=page_size, has_more=has_more)


async def get_card(session: AsyncSession, card_id: int) -> Card:
    card_id = parse_card_id(card_id)
    card = await session.get(Card, card_id)
    if card is None:
        raise BizException("卡密不存在", code=BizCode.NOT_FOUND)
    show(card, "secret", reveal(card.secret) or "")
    return card


@audit("card.update")
@transactional
async def update_available_card(session: AsyncSession, card_id: int, secret: str) -> Card:
    """只改可用卡密的原文。库存数量不变。同一商品下原文不能重复。"""
    card_id = parse_card_id(card_id)
    plain = _one_secret(secret)
    stored = protect(plain) or ""
    card = await session.get(Card, card_id)
    if card is None:
        raise BizException("卡密不存在", code=BizCode.NOT_FOUND)
    if card.status != CardStatus.AVAILABLE:
        raise BizException("只有未使用的卡密可以修改", code=BizCode.CONFLICT)
    taken = await session.scalar(
        select(Card.id).where(
            Card.product_id == card.product_id,
            Card.id != card.id,
            or_(Card.secret == stored, Card.secret == plain),
        )
    )
    if taken is not None:
        raise BizException("卡密已存在", code=BizCode.CONFLICT)
    card.secret = stored
    await session.flush()
    show(card, "secret", plain)
    return card


@transactional
async def reserve_card(session: AsyncSession, order_id: int) -> Card:
    """下单后立即占用一张可用卡密，支付完成前别人不能再拿走。"""
    cards = await reserve_cards(session, order_id, 1)
    return cards[0]


@transactional
async def reserve_cards(session: AsyncSession, order_id: int, quantity: int) -> list[Card]:
    """一次占用多张可用卡密。数量不够时整笔回滚。"""
    if type(quantity) is not int or not 1 <= quantity <= _QUANTITY_MAX:
        raise BizException("购买数量无效", code=BizCode.BAD_REQUEST)
    order = await _require_order(session, order_id)
    await _reject_if_held(session, order.id)
    await _change_card_stock(session, order.product_id, order.spec_id, available=-quantity, reserved=quantity)
    await _change_display_stock(session, order.product_id, order.spec_id, -quantity)
    card_ids = [await _claim_available(session, order, CardStatus.RESERVED, sold=False) for _ in range(quantity)]
    result = await session.scalars(select(Card).where(Card.id.in_(card_ids)))
    return list(result.all())


@transactional
async def release_card(session: AsyncSession, card_id: int) -> Card:
    """订单未支付关闭时，把占用的卡密放回可用库存。"""
    card_id = parse_card_id(card_id)
    result = await session.execute(
        update(Card)
        .where(Card.id == card_id, Card.status == CardStatus.RESERVED)
        .values(status=CardStatus.AVAILABLE, order_id=None)
        .returning(Card.product_id, Card.spec_id)
    )
    row = result.one_or_none()
    if row is None:
        raise BizException("只有占用中的卡密可以释放", code=BizCode.CONFLICT)
    await _change_card_stock(session, row.product_id, row.spec_id, available=1, reserved=-1)
    await _change_display_stock(session, row.product_id, row.spec_id, 1)
    return await session.get(Card, card_id, populate_existing=True)


@transactional
async def sell_card(session: AsyncSession, order_id: int, *, card_id: int | None = None) -> Card:
    """支付完成后把卡密标为已售。

    传入 card_id 时，只接受该订单已经占用的卡密，不再次扣减展示库存。
    不传 card_id 时，从可用库存原子取一张并扣减库存。
    """
    order = await _require_order(session, order_id)
    if card_id is None:
        await _reject_if_held(session, order.id)
        await _change_card_stock(session, order.product_id, order.spec_id, available=-1, sold=1)
        await _change_display_stock(session, order.product_id, order.spec_id, -1)
        taken_id = await _claim_available(session, order, CardStatus.SOLD, sold=True)
        return await session.get(Card, taken_id, populate_existing=True)
    card_id = parse_card_id(card_id)
    result = await session.execute(
        update(Card)
        .where(Card.id == card_id, Card.order_id == order.id, Card.status == CardStatus.RESERVED)
        .values(status=CardStatus.SOLD, sold_at=func.now())
        .returning(Card.id)
    )
    if result.scalar_one_or_none() is None:
        raise BizException("卡密不在占用中", code=BizCode.CONFLICT)
    await _change_card_stock(session, order.product_id, order.spec_id, reserved=-1, sold=1)
    return await session.get(Card, card_id, populate_existing=True)


@transactional
async def sell_reserved_cards(session: AsyncSession, order_id: int) -> list[Card]:
    """把该订单占用的卡密全部改为已售，不再扣展示库存。"""
    order = await _require_order(session, order_id)
    result = await session.execute(
        update(Card)
        .where(Card.order_id == order.id, Card.status == CardStatus.RESERVED)
        .values(status=CardStatus.SOLD, sold_at=func.now())
        .returning(Card.id)
    )
    card_ids = list(result.scalars().all())
    if len(card_ids) != order.product_number:
        raise BizException("占用的卡密数量不符", code=BizCode.CONFLICT)
    await _change_card_stock(session, order.product_id, order.spec_id, reserved=-len(card_ids), sold=len(card_ids))
    found = await session.scalars(select(Card).where(Card.id.in_(card_ids)))
    return list(found.all())


@transactional
async def release_reserved_cards(session: AsyncSession, order_id: int) -> list[Card]:
    """关闭订单时，把该订单占用的卡密全部放回可用库存。"""
    order = await _require_order(session, order_id)
    result = await session.execute(
        update(Card)
        .where(Card.order_id == order.id, Card.status == CardStatus.RESERVED)
        .values(status=CardStatus.AVAILABLE, order_id=None)
        .returning(Card.id, Card.product_id, Card.spec_id)
    )
    rows = list(result.all())
    if len(rows) != order.product_number:
        raise BizException("占用的卡密数量不符", code=BizCode.CONFLICT)
    await _change_card_stock(session, order.product_id, order.spec_id, available=len(rows), reserved=-len(rows))
    await _change_display_stock(session, order.product_id, order.spec_id, len(rows))
    found = await session.scalars(select(Card).where(Card.id.in_([row.id for row in rows])))
    return list(found.all())


@audit("card.discard")
@transactional
async def discard_card(session: AsyncSession, card_id: int) -> Card:
    card_id = parse_card_id(card_id)
    result = await session.execute(
        update(Card)
        .where(Card.id == card_id, Card.status == CardStatus.AVAILABLE)
        .values(status=CardStatus.DISCARDED)
        .returning(Card.product_id, Card.spec_id)
    )
    row = result.one_or_none()
    if row is None:
        if await session.get(Card, card_id) is None:
            raise BizException("卡密不存在", code=BizCode.NOT_FOUND)
        raise BizException("只有未使用的卡密可以作废", code=BizCode.CONFLICT)
    await _change_card_stock(session, row.product_id, row.spec_id, available=-1, discarded=1)
    await _change_display_stock(session, row.product_id, row.spec_id, -1)
    return await session.get(Card, card_id, populate_existing=True)


async def _import_target(
    session: AsyncSession,
    product_id: int,
    spec_id: int | None,
) -> tuple[Product, ProductSpec | None]:
    product, _detail = await get_product(session, product_id)
    has_specs = await _has_specs(session, product.id)
    if has_specs and spec_id is None:
        raise BizException("该商品有规格，请选择规格", code=BizCode.BAD_REQUEST)
    if not has_specs and spec_id is not None:
        raise BizException("该商品没有规格", code=BizCode.BAD_REQUEST)
    if spec_id is None:
        return product, None
    spec = await session.get(ProductSpec, spec_id)
    if spec is None or spec.product_id != product.id:
        raise BizException("规格不存在", code=BizCode.NOT_FOUND)
    return product, spec


async def _has_specs(session: AsyncSession, product_id: int) -> bool:
    spec_id = await session.scalar(select(ProductSpec.id).where(ProductSpec.product_id == product_id).limit(1))
    return spec_id is not None


async def _store_import(
    session: AsyncSession,
    product: Product,
    spec: ProductSpec | None,
    accepted: list[tuple[int, str]],
    failures: list[str],
    total: int,
) -> tuple[CardImportBatch, list[str]]:
    if total == 0:
        raise BizException("没有可导入的卡密", code=BizCode.BAD_REQUEST)
    ready: list[str] = []
    if accepted:
        sealed = {secret: protect(secret) or "" for secret in {item for _line_no, item in accepted}}
        existing = await session.scalars(
            select(Card.secret).where(
                Card.product_id == product.id,
                or_(Card.secret.in_(list(sealed.values())), Card.secret.in_(list(sealed))),
            )
        )
        taken = set(existing.all())
        for line_no, secret in accepted:
            stored = sealed[secret]
            if stored in taken or secret in taken:
                failures.append(f"第 {line_no} 行：卡密已存在")
                continue
            taken.add(stored)
            ready.append(stored)
    success = len(ready)
    if product.stock + success > _STOCK_MAX or (spec is not None and spec.stock + success > _STOCK_MAX):
        raise BizException("导入后库存超出范围", code=BizCode.CONFLICT)
    batch = CardImportBatch(
        product_id=product.id,
        product_name=product.name,
        total_count=total,
        success_count=success,
        failure_count=len(failures),
    )
    session.add(batch)
    await session.flush()
    spec_id = None if spec is None else spec.id
    if ready:
        await session.execute(
            insert(Card),
            [
                {
                    "batch_id": batch.id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "spec_id": spec_id,
                    "secret": secret,
                    "status": CardStatus.AVAILABLE,
                }
                for secret in ready
            ],
        )
    if success:
        stock = await _stock_row(session, product.id, spec_id)
        if stock is None:
            stock = CardStock(
                product_id=product.id,
                product_name=product.name,
                spec_id=spec_id,
                total_count=0,
                sold_count=0,
                available_count=0,
                reserved_count=0,
                discarded_count=0,
            )
            session.add(stock)
        stock.product_name = product.name
        stock.total_count += success
        stock.available_count += success
        product.stock += success
        if spec is not None:
            spec.stock += success
    await session.flush()
    return batch, failures


async def _stock_row(session: AsyncSession, product_id: int, spec_id: int | None) -> CardStock | None:
    statement = select(CardStock).where(CardStock.product_id == product_id)
    if spec_id is None:
        statement = statement.where(CardStock.spec_id.is_(None))
    else:
        statement = statement.where(CardStock.spec_id == spec_id)
    return await session.scalar(statement.limit(1))


def _one_secret(value: str) -> str:
    if not isinstance(value, str):
        raise BizException("卡密内容不能为空", code=BizCode.BAD_REQUEST)
    secret = value.strip()
    if not secret:
        raise BizException("卡密内容不能为空", code=BizCode.BAD_REQUEST)
    if len(secret) > _SECRET_MAX:
        raise BizException("卡密不能超过 2000 个字符", code=BizCode.BAD_REQUEST)
    if any(ord(char) < 32 or ord(char) == 127 for char in secret):
        raise BizException("卡密包含非法字符", code=BizCode.BAD_REQUEST)
    return secret


def _split_lines(text: str) -> tuple[list[tuple[int, str]], list[str], int]:
    accepted: list[tuple[int, str]] = []
    failures: list[str] = []
    seen: set[str] = set()
    total = 0
    for line_no, raw in enumerate(text.splitlines(), start=1):
        secret = raw.strip()
        if not secret:
            continue
        total += 1
        if total > _LINE_MAX:
            raise BizException("一次最多导入 500 条卡密", code=BizCode.BAD_REQUEST)
        if len(secret) > _SECRET_MAX:
            failures.append(f"第 {line_no} 行：卡密不能超过 2000 个字符")
            continue
        if any(ord(char) < 32 or ord(char) == 127 for char in secret):
            failures.append(f"第 {line_no} 行：卡密包含非法字符")
            continue
        if secret in seen:
            failures.append(f"第 {line_no} 行：卡密重复")
            continue
        seen.add(secret)
        accepted.append((line_no, secret))
    return accepted, failures, total


async def _require_order(session: AsyncSession, order_id: int) -> Order:
    if type(order_id) is not int or order_id <= 0:
        raise BizException("订单编号无效", code=BizCode.BAD_REQUEST)
    order = await session.get(Order, order_id)
    if order is None:
        raise BizException("订单不存在", code=BizCode.NOT_FOUND)
    return order


async def require_available_cards(
    session: AsyncSession,
    product: Product,
    spec: ProductSpec | None,
    quantity: int,
) -> None:
    """写事务内、创建订单前调用。可用数量不够时直接拒绝。"""
    if type(quantity) is not int or not 1 <= quantity <= _QUANTITY_MAX:
        raise BizException("购买数量无效", code=BizCode.BAD_REQUEST)
    spec_id = None if spec is None else spec.id
    if product.stock < quantity or (spec is not None and spec.stock < quantity):
        raise BizException("库存不足", code=BizCode.CONFLICT)
    stock = await _stock_row(session, product.id, spec_id)
    if stock is None or stock.available_count < quantity:
        raise BizException("库存不足", code=BizCode.CONFLICT)
    statement = select(func.count()).select_from(Card).where(
        Card.product_id == product.id,
        Card.status == CardStatus.AVAILABLE,
    )
    if spec_id is None:
        statement = statement.where(Card.spec_id.is_(None))
    else:
        statement = statement.where(Card.spec_id == spec_id)
    available = await session.scalar(statement)
    if available is None or available < quantity:
        raise BizException("库存不足", code=BizCode.CONFLICT)


async def _reject_if_held(session: AsyncSession, order_id: int) -> None:
    held = await session.scalar(
        select(Card.id).where(
            Card.order_id == order_id,
            Card.status.in_((CardStatus.RESERVED, CardStatus.SOLD)),
        )
    )
    if held is not None:
        raise BizException("订单已占用卡密", code=BizCode.CONFLICT)


async def _claim_available(session: AsyncSession, order: Order, status: str, *, sold: bool) -> int:
    candidate = select(Card.id).where(Card.product_id == order.product_id, Card.status == CardStatus.AVAILABLE)
    if order.spec_id is None:
        candidate = candidate.where(Card.spec_id.is_(None))
    else:
        candidate = candidate.where(Card.spec_id == order.spec_id)
    values: dict[str, object] = {"status": status, "order_id": order.id}
    if sold:
        values["sold_at"] = func.now()
    result = await session.execute(
        update(Card)
        .where(Card.id == candidate.order_by(Card.id).limit(1).scalar_subquery(), Card.status == CardStatus.AVAILABLE)
        .values(**values)
        .returning(Card.id)
    )
    card_id = result.scalar_one_or_none()
    if card_id is None:
        raise BizException("库存不足", code=BizCode.CONFLICT)
    return card_id


async def _change_card_stock(
    session: AsyncSession,
    product_id: int,
    spec_id: int | None,
    *,
    available: int = 0,
    reserved: int = 0,
    sold: int = 0,
    discarded: int = 0,
) -> None:
    """条件更新计数。减到不够时影响行数为 0，调用方回滚，不会出现负数。"""
    stock = await _stock_row(session, product_id, spec_id)
    if stock is None:
        raise BizException("库存不足", code=BizCode.CONFLICT)
    conditions = [CardStock.id == stock.id]
    values: dict[str, object] = {}
    for delta, column, name in (
        (available, CardStock.available_count, "available_count"),
        (reserved, CardStock.reserved_count, "reserved_count"),
        (sold, CardStock.sold_count, "sold_count"),
        (discarded, CardStock.discarded_count, "discarded_count"),
    ):
        if not delta:
            continue
        if delta < 0:
            conditions.append(column >= -delta)
        values[name] = column + delta
    result = await session.execute(update(CardStock).where(*conditions).values(**values))
    if result.rowcount != 1:
        raise BizException("库存不足", code=BizCode.CONFLICT)


async def _change_display_stock(session: AsyncSession, product_id: int, spec_id: int | None, delta: int) -> None:
    """展示库存同样用 stock >= 1 这种条件更新。减不下去就回滚。"""
    if delta == 0:
        return
    if delta < 0:
        product_where = (Product.id == product_id, Product.stock >= -delta)
        spec_where = (ProductSpec.id == spec_id, ProductSpec.stock >= -delta)
        message = "库存不足"
    else:
        product_where = (Product.id == product_id, Product.stock <= _STOCK_MAX - delta)
        spec_where = (ProductSpec.id == spec_id, ProductSpec.stock <= _STOCK_MAX - delta)
        message = "库存超出范围"
    result = await session.execute(update(Product).where(*product_where).values(stock=Product.stock + delta))
    if result.rowcount != 1:
        raise BizException(message, code=BizCode.CONFLICT)
    if spec_id is None:
        return
    result = await session.execute(update(ProductSpec).where(*spec_where).values(stock=ProductSpec.stock + delta))
    if result.rowcount != 1:
        raise BizException(message, code=BizCode.CONFLICT)


def _read_text_file(path_text: str) -> str:
    path = Path(path_text)
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        raise BizException("只支持 .txt 文件", code=BizCode.BAD_REQUEST)
    if not path.is_file():
        raise BizException("文件不存在", code=BizCode.NOT_FOUND)
    if path.stat().st_size > _FILE_MAX:
        raise BizException("文件不能超过 1MB", code=BizCode.BAD_REQUEST)
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BizException("文件编码必须是 UTF-8", code=BizCode.BAD_REQUEST) from exc
