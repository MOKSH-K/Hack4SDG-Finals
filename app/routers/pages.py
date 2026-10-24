from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
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


# Browsers and iOS look for these at the site root regardless of <link> tags.
ICONS = BASE_DIR / "static" / "icons"
ROOT_FILES = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/apple-touch-icon-precomposed.png": ("apple-touch-icon.png", "image/png"),
}


def _make_file(name: str, media_type: str):
    def file():
        return FileResponse(ICONS / name, media_type=media_type, headers={"Cache-Control": "public, max-age=604800"})
    return file


for path, (name, media_type) in ROOT_FILES.items():
    router.add_api_route(path, _make_file(name, media_type), methods=["GET"])
