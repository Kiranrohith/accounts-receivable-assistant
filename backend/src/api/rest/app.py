from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.middleware.cors import add_cors
from api.middleware.error_handler import register_error_handlers
from api.rest.customers.routes import router as customers_router
from api.rest.documents.routes import router as documents_router
from api.rest.documents.type_reviews import router as type_reviews_router
from api.rest.fx.routes import router as fx_router
from api.rest.invoices.listing import router as invoice_listing_router
from api.rest.invoices.reviews import router as reviews_router
from api.rest.invoices.version_reviews import router as version_reviews_router
from api.rest.purchase_orders.listing import router as po_listing_router
from api.rest.purchase_orders.reviews import mismatch_router as po_mismatch_router
from api.rest.purchase_orders.reviews import router as po_reviews_router
from api.rest.platform.health import router as health_router
from api.rest.users.admin import router as users_router
from api.rest.users.auth import router as auth_router
from config.settings import get_settings
from data.clients.db import engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    worker = None
    if get_settings().run_embedded_worker:  # dev: one process does both; production runs jobs.runner
        from jobs.runner import start_worker

        worker = await start_worker()
    yield
    if worker is not None:
        await worker.stop()
    await engine.dispose()


app = FastAPI(title="Genworx AR Assistant API", lifespan=lifespan)
add_cors(app)
register_error_handlers(app)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(customers_router)
# the one upload entry for invoices and purchase orders (/documents/upload)
app.include_router(documents_router)
app.include_router(reviews_router)
app.include_router(version_reviews_router)
app.include_router(type_reviews_router)
app.include_router(fx_router)
app.include_router(po_reviews_router)
app.include_router(po_mismatch_router)
# after the other /purchase-orders/... routers: its /{purchase_order_id} path would shadow them
app.include_router(po_listing_router)
# last among the /invoices/... routers: its /{invoice_id} path would shadow the static ones
app.include_router(invoice_listing_router)
