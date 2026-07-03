import asyncio
import io
import unittest
from datetime import date

from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes import amount_in_sgd, create_budget_log, delete_invoice, upload_invoice
from database.session import Base
from models.entities import BudgetLog, Invoice, Project
from models.schemas import BudgetLogCreate


class FinanceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        project = Project(name="Multi-currency test", budget=1000)
        self.db.add(project)
        self.db.commit()
        self.project_id = project.id

    def tearDown(self):
        self.db.close()

    def test_conversion_rounds_to_sgd_cents(self):
        self.assertEqual(amount_in_sgd(248.37, 1.3526), 335.95)

    def test_foreign_currency_expense_preserves_original_values(self):
        payload = BudgetLogCreate(
            project_id=self.project_id,
            category="Freight",
            currency="usd",
            original_amount=248.37,
            exchange_rate_to_sgd=1.3526,
            amount=0,
            date=date(2026, 7, 3),
        )
        log = asyncio.run(create_budget_log(payload, "test-token", self.db))
        self.assertEqual(log.currency, "USD")
        self.assertEqual(log.original_amount, 248.37)
        self.assertEqual(log.exchange_rate_to_sgd, 1.3526)
        self.assertEqual(log.amount, 335.95)

    def test_invoice_creates_and_deletes_one_linked_expense(self):
        upload = UploadFile(filename="invoice.pdf", file=io.BytesIO(b"%PDF-1.4\n%%EOF"))
        upload.headers = {"content-type": "application/pdf"}
        invoice = asyncio.run(
            upload_invoice(
                "test-token",
                project_id=self.project_id,
                team_id=None,
                category="Components",
                description="USD component order",
                invoice_date=date(2026, 7, 3),
                currency="USD",
                original_amount=100,
                exchange_rate_to_sgd=1.35,
                sponsored_by="",
                file=upload,
                db=self.db,
            )
        )
        self.assertIsNotNone(invoice.budget_log_id)
        linked_log = self.db.get(BudgetLog, invoice.budget_log_id)
        self.assertEqual(linked_log.amount, 135)
        self.assertEqual(self.db.query(Invoice).count(), 1)
        asyncio.run(delete_invoice(invoice.id, "test-token", self.db))
        self.assertEqual(self.db.query(Invoice).count(), 0)
        self.assertIsNone(self.db.get(BudgetLog, linked_log.id))


if __name__ == "__main__":
    unittest.main()
