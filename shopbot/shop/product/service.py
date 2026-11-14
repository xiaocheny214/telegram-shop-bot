"""商品和规格的增删改查。

入参格式由 Pydantic 校验。写入和查询使用 ORM 绑定参数，不把输入拼进 SQL。
已售数量不从入参修改。新增、修改、删除在事务中执行。FastAPI 已开启事务时并入该事务。
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.db import transactional
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.catalog.service import get_category
from shopbot.shop.models import Card, CardStock, Order, Product, ProductDetail, ProductSpec
from shopbot.shop.product.schemas import (
    ProductCreate,
    ProductUpdate,
    SpecCreate,
    SpecUpdate,
    parse_model,
    parse_product_id,
    parse_spec_id,
)
from shopbot.system.audit.service import audit
from shopbot.system.vault import protect, reveal, show


@audit("product.create")
@transactional
async def create_product(session: AsyncSession, payload: ProductCreate | dict) -> tuple[Product, ProductDetail]:
    data = parse_model(ProductCreate, payload)
    await get_category(session, data.category_id)
    await _available_name(session, data.category_id, data.name)
    product = Product(
        category_id=data.category_id,
        name=data.name,
        cover_url=data.cover_url,
        currency=data.currency,
        price_cents=data.price_cents,
        delivery_method=data.delivery_method,
        stock_deduct_strategy=data.stock_deduct_strategy,
        stock=data.stock,
        sold_count=0,
        sort_order=data.sort_order,
        is_active=data.is_active,
    )
    session.add(product)
    await session.flush()
    detail = ProductDetail(
        product_id=product.id,
        description=data.description,
        internal_remark=protect(data.internal_remark),
    )
    session.add(detail)
    await session.flush()
    show(detail, "internal_remark", data.internal_remark)
    return product, detail



@transactional
async def get_product(session: AsyncSession, product_id: int) -> tuple[Product, ProductDetail]:
    product = await session.get(Product, parse_product_id(product_id))
    if product is None:
        raise BizException("商品不存在", code=BizCode.NOT_FOUND)
    detail = await session.get(ProductDetail, product.id)
    if detail is None:
        detail = ProductDetail(product_id=product.id, description="", internal_remark=None)
        session.add(detail)
        await session.flush()
    show(detail, "internal_remark", reveal(detail.internal_remark))
    return product, detail


async def list_products(
    session: AsyncSession,
    *,
    category_id: int | None = None,
    active_only: bool = False,
) -> list[Product]:
    statement = select(Product).order_by(Product.sort_order, Product.id)
    if category_id is not None:
        await get_category(session, category_id)
        statement = statement.where(Product.category_id == category_id)
    if active_only:
        statement = statement.where(Product.is_active.is_(True))
    result = await session.scalars(statement.limit(200))
    return list(result.all())


@audit("product.update")
@transactional
async def update_product(
    session: AsyncSession,
    product_id: int,
    payload: ProductUpdate | dict,
) -> tuple[Product, ProductDetail]:
    data = parse_model(ProductUpdate, payload)
    product, detail = await get_product(session, product_id)
    category_id = product.category_id if data.category_id is None else data.category_id
    if data.category_id is not None:
        await get_category(session, data.category_id)
        product.category_id = data.category_id
    if data.name is not None or data.category_id is not None:
        name = product.name if data.name is None else data.name
        await _available_name(session, category_id, name, exclude_id=product.id)
        if data.name is not None and data.name != product.name:
            product.name = data.name
            await session.execute(
                update(CardStock).where(CardStock.product_id == product.id).values(product_name=data.name)
            )
    if "cover_url" in data.model_fields_set:
        product.cover_url = data.cover_url
    if data.currency is not None:
        product.currency = data.currency
    if data.price_cents is not None:
        product.price_cents = data.price_cents
    if data.delivery_method is not None:
        product.delivery_method = data.delivery_method
    if data.stock_deduct_strategy is not None:
        product.stock_deduct_strategy = data.stock_deduct_strategy
    if data.stock is not None:
        product.stock = data.stock
    if data.sort_order is not None:
        product.sort_order = data.sort_order
    if data.is_active is not None:
        product.is_active = data.is_active
    if data.description is not None:
        detail.description = data.description
    plain_remark = detail.internal_remark
    if "internal_remark" in data.model_fields_set:
        plain_remark = data.internal_remark
        detail.internal_remark = protect(data.internal_remark)
    await session.flush()
    show(detail, "internal_remark", plain_remark if "internal_remark" in data.model_fields_set else reveal(plain_remark))
    return product, detail


@audit("product.delete")
@transactional
async def delete_product(session: AsyncSession, product_id: int) -> None:
    product, detail = await get_product(session, product_id)
    if await session.scalar(select(Order.id).where(Order.product_id == product.id)):
        raise BizException("商品已有订单，不能删除", code=BizCode.CONFLICT)
    if await session.scalar(select(Card.id).where(Card.product_id == product.id)):
        raise BizException("商品还有卡密，不能删除", code=BizCode.CONFLICT)
    specs = await session.scalars(select(ProductSpec).where(ProductSpec.product_id == product.id))
    for spec in specs.all():
        await session.delete(spec)
    stocks = await session.scalars(select(CardStock).where(CardStock.product_id == product.id))
    for stock in stocks.all():
        await session.delete(stock)
    await session.delete(detail)
    await session.delete(product)
    await session.flush()


@audit("spec.create")
@transactional
async def create_spec(session: AsyncSession, product_id: int, payload: SpecCreate | dict) -> ProductSpec:
    data = parse_model(SpecCreate, payload)
    product, _detail = await get_product(session, product_id)
    await _available_spec_name(session, product.id, data.name)
    spec = ProductSpec(
        product_id=product.id,
        name=data.name,
        price_cents=data.price_cents,
        stock=data.stock,
        sold_count=0,
        sort_order=data.sort_order,
        is_active=data.is_active,
    )
    session.add(spec)
    await session.flush()
    return spec


async def list_specs(session: AsyncSession, product_id: int, *, active_only: bool = False) -> list[ProductSpec]:
    product, _detail = await get_product(session, product_id)
    statement = select(ProductSpec).where(ProductSpec.product_id == product.id).order_by(ProductSpec.sort_order, ProductSpec.id)
    if active_only:
        statement = statement.where(ProductSpec.is_active.is_(True))
    result = await session.scalars(statement.limit(100))
    return list(result.all())


@audit("spec.update")
@transactional
async def update_spec(session: AsyncSession, spec_id: int, payload: SpecUpdate | dict) -> ProductSpec:
    data = parse_model(SpecUpdate, payload)
    spec = await _get_spec(session, spec_id)
    if data.name is not None:
        await _available_spec_name(session, spec.product_id, data.name, exclude_id=spec.id)
        spec.name = data.name
    if data.price_cents is not None:
        spec.price_cents = data.price_cents
    if data.stock is not None:
        spec.stock = data.stock
    if data.sort_order is not None:
        spec.sort_order = data.sort_order
    if data.is_active is not None:
        spec.is_active = data.is_active
    await session.flush()
    return spec


@audit("spec.delete")
@transactional
async def delete_spec(session: AsyncSession, spec_id: int) -> None:
    spec = await _get_spec(session, spec_id)
    if await session.scalar(select(Order.id).where(Order.spec_id == spec.id)):
        raise BizException("规格已有订单，不能删除", code=BizCode.CONFLICT)
    if await session.scalar(select(Card.id).where(Card.spec_id == spec.id)):
        raise BizException("规格还有卡密，不能删除", code=BizCode.CONFLICT)
    await session.delete(spec)
    await session.flush()


async def _get_spec(session: AsyncSession, spec_id: int) -> ProductSpec:
    spec = await session.get(ProductSpec, parse_spec_id(spec_id))
    if spec is None:
        raise BizException("规格不存在", code=BizCode.NOT_FOUND)
    return spec


async def _available_name(
    session: AsyncSession,
    category_id: int,
    name: str,
    *,
    exclude_id: int | None = None,
) -> None:
    statement = select(Product.id).where(Product.category_id == category_id, Product.name == name)
    if exclude_id is not None:
        statement = statement.where(Product.id != exclude_id)
    if await session.scalar(statement):
        raise BizException("同分类下商品名称已存在", code=BizCode.CONFLICT)


async def _available_spec_name(
    session: AsyncSession,
    product_id: int,
    name: str,
    *,
    exclude_id: int | None = None,
) -> None:
    statement = select(ProductSpec.id).where(ProductSpec.product_id == product_id, ProductSpec.name == name)
    if exclude_id is not None:
        statement = statement.where(ProductSpec.id != exclude_id)
    if await session.scalar(statement):
        raise BizException("规格名称已存在", code=BizCode.CONFLICT)
