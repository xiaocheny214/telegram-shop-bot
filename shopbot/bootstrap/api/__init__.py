"""HTTP 路由集合。"""

from fastapi import APIRouter

from shopbot.bootstrap.api.admin import router as admin_router
from shopbot.bootstrap.api.payments import router as payments_router

router = APIRouter()
router.include_router(payments_router)
router.include_router(admin_router)
