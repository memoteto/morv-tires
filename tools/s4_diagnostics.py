"""Measure S4 baseline geometry and authorization with disposable users and databases."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def report(kind, **values):
    print(kind, json.dumps(values, ensure_ascii=False), flush=True)


def security_probe(con, admin):
    from morv.infra import users
    from morv.ui.session import Session

    cashier = users.create_user(con, admin, "probe_cashier", "Test cashier", "TestCash123", "cashier")
    manager = users.create_user(con, admin, "probe_manager", "Test manager", "TestBoss123", "manager")
    session = Session(con, cashier)

    def check(name, actual, expected):
        report("SECURITY", name=name, actual=actual, expected=expected, matches=actual == expected)

    users.set_permission(con, admin, cashier.id, "items.manage", True)
    check("cashier_grant", session.can("items.manage"), True)
    users.set_permission(con, admin, cashier.id, "items.manage", False)
    check("cashier_revoke", session.can("items.manage"), False)
    users.set_role(con, admin, cashier.id, "manager")
    check("promoted_discount_limit", users.permission_limit(con, cashier, "sales.discount"), None)
    users.set_role(con, admin, cashier.id, "cashier")
    manager_session = Session(con, manager)
    users.set_role(con, admin, manager.id, "cashier")
    check("demoted_database_role", users.get_user(con, manager.id).role, "cashier")
    check("demoted_session_permission", manager_session.can("users.manage"), False)
    check("demoted_discount_limit", users.permission_limit(con, manager, "sales.discount"), 0)
    before = users.permissions(con, cashier)
    try:
        users.set_permission(con, manager, cashier.id, "items.manage", True)
    except users.AuthError:
        refused = True
    else:
        refused = False
    check("demoted_write_refused", refused, True)
    check("demoted_write_preserved_permissions", users.permissions(con, cashier) == before, True)
    users.set_active(con, admin, cashier.id, False)
    check("inactive_cashier_permission", session.can("sales.post"), False)
    users.set_role(con, admin, manager.id, "manager")
    users.set_active(con, admin, manager.id, False)
    check("inactive_manager_permission", manager_session.can("users.manage"), False)


def geometry_probe(con, admin):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QAbstractButton, QScrollArea
    from tests.qt_app import get_app
    from morv.infra import users
    from morv.ui import theme
    from morv.ui.session import Session
    from morv.ui.shell import MainWindow
    from morv.ui.widgets import dispose

    cashier = users.create_user(con, admin, "layout_cashier", "Test cashier", "TestCash123", "cashier")
    app = get_app()
    theme.load_bundled_fonts()
    win = MainWindow(Session(con, admin), app)
    win.health_timer.stop()
    try:
        win.show()
        win.go_key("settings")
        page = win.pages["settings"]
        assert page.open_page("users"), "users route did not open"
        tab = page.users_tab
        scrolls = tab.detail.findChildren(QScrollArea)
        assert len(scrolls) == 1, "unexpected permissions scroll count"
        scroll = scrolls[0]

        def settle():
            for _ in range(12):
                app.processEvents()

        def box(w, root):
            p = w.mapTo(root, QPoint())
            return [p.x(), p.y(), w.width(), w.height()]

        def minimum(w):
            return [w.minimumSizeHint().width(), w.minimumSizeHint().height()]

        count = 0
        for width, height in ((1093, 614), (1366, 768), (1920, 1080)):
            win.setMinimumSize(min(1024, width - 40), min(640, height - 80))
            win.resize(width, height - 40)
            for dark in (False, True):
                if win.dark != dark:
                    win.toggle_theme()
                for collapsed in (False, True):
                    if win.collapsed != collapsed:
                        win.toggle_sidebar()
                    for font in theme.FONT_SIZES:
                        win.set_font(theme.DEFAULT_FONT, font)
                        for target in (admin, cashier):
                            tab.reload(target.id)
                            settle()
                            assert tab.current.id == target.id, "unexpected selected user"
                            content = scroll.widget()
                            scroll.verticalScrollBar().setValue(0)
                            settle()
                            buttons = [w for w in tab.findChildren(QAbstractButton)
                                       if w.isVisible() and w.text() and w not in tab.checks.values()]
                            rects = [box(w, tab) for w in buttons]
                            bounds = tab.rect()
                            bad_buttons = [
                                w.text() for w, r in zip(buttons, rects, strict=True)
                                if not bounds.contains(QRect(*r)) or w.width() < w.minimumSizeHint().width()
                                or w.height() < w.minimumSizeHint().height()
                            ]
                            narrow_checks = [k for k, w in tab.checks.items()
                                             if w.width() < w.sizeHint().width()]
                            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                            settle()
                            count += 1
                            report("GEOMETRY", screen=[width, height], window=[win.width(), win.height()],
                                   dark=dark, collapsed=collapsed, font=font, role=target.role,
                                   page=box(tab, win), user_list=box(tab.list, tab), list_min=minimum(tab.list),
                                   detail=box(tab.detail, tab), detail_min=minimum(tab.detail),
                                   scroll=box(scroll, tab), viewport=[scroll.viewport().width(), scroll.viewport().height()],
                                   content=[content.width(), content.height()], content_min=minimum(content),
                                   grid_min=content.layout().minimumSize().width(),
                                   permission_columns=tab.permission_grid.columns,
                                   right_pane_min=tab.layout().itemAt(0).layout().minimumSize().width(),
                                   max_check_hint=max(w.sizeHint().width() for w in tab.checks.values()),
                                   narrow_checks=narrow_checks, horizontal_max=scroll.horizontalScrollBar().maximum(),
                                   vertical_max=scroll.verticalScrollBar().maximum(), bad_buttons=bad_buttons,
                                   buttons=[(w.text(), r, minimum(w)) for w, r in zip(buttons, rects, strict=True)],
                                   actions_moved=rects != [box(w, tab) for w in buttons],
                                   discount_text=tab.discount_cap.text(), discount_enabled=tab.discount_cap.isEnabled())
        report("GEOMETRY_COMPLETE", cases=count, expected=144)
        assert count == 144
    finally:
        dispose(win, app)


def main():
    from morv.infra import users
    from morv.infra.db.bootstrap import open_main_db

    with tempfile.TemporaryDirectory(prefix="morv-s4-") as tmp:
        with patch.dict(os.environ, {"MORV_DATA_DIR": tmp}):
            con = open_main_db(Path(tmp) / "diagnostic.db")
            try:
                admin = users.create_user(con, None, "admin", "Test admin", "TestAdmin123", "manager")
                security_probe(con, admin)
                geometry_probe(con, admin)
            finally:
                con.close()


if __name__ == "__main__":
    main()
