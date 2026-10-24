from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# path -> (template, page title, roles allowed; empty = public)
PAGES = {
    "/": ("index.html", "Feeding the night shift. Rescuing every meal.", ()),
    "/login": ("login.html", "Sign in", ()),
    "/restaurant": ("restaurant.html", "Restaurant Operations Hub", ("restaurant",)),
    "/darkstore": ("darkstore.html", "Dark Store Manager", ("darkstore",)),
    "/store": ("store.html", "Late-Night Store", ()),
    "/ngo": ("ngo.html", "NGO Relief Portal", ("ngo",)),
    "/analytics": ("analytics.html", "SDG Impact Analytics", ()),
}


def _make_page(template: str, title: str, roles: tuple[str, ...]):
    def page(request: Request):
        return templates.TemplateResponse(request, template, {
            "title": title, "required_roles": list(roles)})
    return page


for path, (template, title, roles) in PAGES.items():
    router.add_api_route(path, _make_page(template, title, roles), methods=["GET"], response_class=HTMLResponse)
