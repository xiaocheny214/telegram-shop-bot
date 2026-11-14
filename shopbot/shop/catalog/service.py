"""分类的增删改查。

入参格式由 Pydantic 校验。写入和查询使用 ORM 绑定参数，不把输入拼进 SQL。
新增、修改、删除在事务中执行。FastAPI 已开启事务时并入该事务。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shopbot.framework.db import transactional
from shopbot.framework.enums.biz_code import BizCode
from shopbot.framework.exceptions.biz import BizException
from shopbot.shop.catalog.schemas import CategoryCreate, CategoryUpdate, parse_category_id, parse_model
from shopbot.shop.models import Category, Product
from shopbot.system.audit.service import audit


@audit("catalog.create")
@transactional
async def create_category(session: AsyncSession, payload: CategoryCreate | dict) -> Category:
    data = parse_model(CategoryCreate, payload)
    category = Category(
        name=await _available_name(session, data.name),
        sort_order=data.sort_order,
        is_active=data.is_active,
    )
    session.add(category)
    await session.flush()
    return category


async def get_category(session: AsyncSession, category_id: int) -> Category:
    category = await session.get(Category, parse_category_id(category_id))
    if category is None:
        raise BizException("分类不存在", code=BizCode.NOT_FOUND)
    return category


async def list_categories(session: AsyncSession, *, active_only: bool = False) -> list[Category]:
    statement = select(Category).order_by(Category.sort_order, Category.id)
    if active_only:
        statement = statement.where(Category.is_active.is_(True))
    result = await session.scalars(statement.limit(200))
    return list(result.all())


@audit("catalog.update")
@transactional
async def update_category(session: AsyncSession, category_id: int, payload: CategoryUpdate | dict) -> Category:
    data = parse_model(CategoryUpdate, payload)
    category = await get_category(session, category_id)
    if data.name is not None:
        category.name = await _available_name(session, data.name, exclude_id=category.id)
    if data.sort_order is not None:
        category.sort_order = data.sort_order
    if data.is_active is not None:
        category.is_active = data.is_active
    await session.flush()
    return category


@audit("catalog.delete")
@transactional
async def delete_category(session: AsyncSession, category_id: int) -> None:
    category = await get_category(session, category_id)
    product_count = await session.scalar(
        select(func.count()).select_from(Product).where(Product.category_id == category.id)
    )
    if product_count:
        raise BizException("分类下还有商品，不能删除", code=BizCode.CONFLICT)
    await session.delete(category)
    await session.flush()


async def _available_name(session: AsyncSession, name: str, *, exclude_id: int | None = None) -> str:
    statement = select(Category.id).where(Category.name == name)
    if exclude_id is not None:
        statement = statement.where(Category.id != exclude_id)
    if await session.scalar(statement):
        raise BizException("分类名称已存在", code=BizCode.CONFLICT)
    return name
