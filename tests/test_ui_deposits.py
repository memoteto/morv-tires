"""Customer deposit dialogs: post a deposit, list documents and reverse the selected one."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import get_app, require_qt


@require_qt()
class DepositDialogsUiTest(unittest.TestCase):
    def setUp(self):
        from morv.infra import users
        from morv.infra.db.bootstrap import open_main_db
        from morv.ui.session import Session
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MORV_DATA_DIR"] = str(self.tmp)
        self.con = open_main_db(self.tmp / "morv_tires.db")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.environ.pop, "MORV_DATA_DIR", None)
        self.addCleanup(self.con.close)
        admin = users.create_user(self.con, None, "admin", "المدير", "Admin12345", "manager")
        self.session = Session(self.con, admin)
        self.app = get_app()
        self.cust = self.con.execute(
            "INSERT INTO customers (name, name_norm, mobile, credit_limit) "
            "VALUES ('عميل وهمي','عميل وهمي','01000000011',0) RETURNING id").fetchone()[0]

    def test_deposit_dialog_posts_and_the_documents_list_offers_a_reversal(self):
        from morv.infra import customer_receipts
        from morv.ui.deposit_dialogs import CustomerDocumentsDialog, DepositDialog
        from morv.ui.widgets import dispose
        dlg = DepositDialog(self.session, self.cust, "عميل وهمي")
        self.addCleanup(dispose, dlg, self.app)
        dlg.amount.setText("500")
        dlg.save()
        self.assertIsNotNone(dlg.saved)
        self.assertEqual(customer_receipts.deposit_balance(self.con, self.cust), 50000)
        docs = CustomerDocumentsDialog(self.session, self.cust, "عميل وهمي")
        self.addCleanup(dispose, docs, self.app)
        self.assertEqual(docs.docs.count(), 1)
        self.assertTrue(docs.reverse_btn.isEnabled())

    def test_kind_switch_hides_the_payment_method_for_an_application(self):
        from morv.ui.deposit_dialogs import DepositDialog
        from morv.ui.widgets import dispose
        dlg = DepositDialog(self.session, self.cust, "عميل وهمي")
        self.addCleanup(dispose, dlg, self.app)
        self.assertFalse(dlg.method.isHidden())
        dlg.kind.setCurrentIndex(dlg.kind.findData("apply"))
        self.assertTrue(dlg.method.isHidden())
        dlg.amount.setText("10")
        dlg.save()
        self.assertIsNone(dlg.saved)                       # no deposit and no debt: refused, nothing posted
