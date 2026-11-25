"""Live customer lookup and quick creation entry point in POS."""
from tests.qt_app import require_qt
from tests.test_ui_pos_customer_lookup import PosFixture


@require_qt()
class PartySearchTest(PosFixture):

    def setUp(self):
        super().setUp()
        from morv.infra import customers

        self.first = customers.create(
            self.con, self.session.user,
            customers.Customer(
                None, "أحمد محمد", "01012345678"
            )
        )

        self.second = customers.create(
            self.con, self.session.user,
            customers.Customer(
                None, "أحمد محمد", "01012398765"
            )
        )

    def type_query(self, p, query):
        from PySide6.QtTest import QTest

        edit = (
            p.customer.lineEdit()
            if hasattr(p.customer, "lineEdit")
            else p.customer
        )

        edit.setFocus()
        edit.selectAll()
        edit.insert(query)

        QTest.qWait(400)
        self.app.processEvents()

    def visible_buttons(self, p):
        from PySide6.QtWidgets import QPushButton

        return [
            b for b in p.findChildren(QPushButton)
            if b.isVisible()
        ]

    def result_button(self, p, name, mobile):
        from PySide6.QtWidgets import QLabel

        for button in self.visible_buttons(p):
            content = " ".join(
                [button.text()] +
                [label.text()
                 for label in button.findChildren(QLabel)]
            )
            if name in content and mobile in content:
                return button

        self.fail(
            f"No visible result before Enter: {name} / {mobile}"
        )

    def test_partial_mobile_shows_name_and_number_live(self):
        p = self.page()
        self.type_query(p, "0101234")

        self.result_button(
            p, self.first.name, self.first.mobile
        )

    def test_partial_name_shows_both_matching_customers(self):
        p = self.page()
        self.type_query(p, "أحمد")

        first = self.result_button(
            p, self.first.name, self.first.mobile
        )
        second = self.result_button(
            p, self.second.name, self.second.mobile
        )

        self.assertIsNot(first, second)

    def test_same_name_selection_posts_to_correct_customer(self):
        item_id = self.con.execute(
            "INSERT INTO items "
            "(kind, code, name, name_norm, "
            "price_retail, price_wholesale, price_super) "
            "VALUES ('service','TEST-SV','Test service',"
            "'test service',100,100,100)"
        ).lastrowid

        p = self.page()
        self.type_query(p, "أحمد")

        button = self.result_button(
            p, self.second.name, self.second.mobile
        )

        button.click()
        self.app.processEvents()

        self.assertIn(self.second.mobile, p.cust_stats.text())

        p._add_row(item_id, "service", "Test service")
        p._rows[-1]["price_edit"].setText("1.00")
        p.pay1_received.setText("1.00")
        p.post()

        self.assertIsNotNone(p.posted)
        self.assertEqual(
            self.con.execute(
                "SELECT customer_id FROM sales"
            ).fetchall(),
            [(self.second.id,)]
        )

    def test_unknown_query_offers_new_customer(self):
        for query in ("عميل جديد تجريبي", "01099999999"):
            with self.subTest(query=query):
                p = self.page()
                self.type_query(p, query)

                buttons = self.visible_buttons(p)

                self.assertTrue(
                    any("عميل جديد" in b.text()
                        for b in buttons),
                    "Missing quick customer creation action"
                )

                self.assertEqual(
                    self.con.execute(
                        "SELECT count(*) FROM customers"
                    ).fetchone()[0],
                    2
                )
