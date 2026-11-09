import asyncio
import unittest

from sqlalchemy import Column, MetaData, String, Table, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.main import get_attendance_summary, should_send_attendance_threshold_alert


class AttendanceThresholdAlertTests(unittest.TestCase):
    def test_waits_until_minimum_marked_days(self) -> None:
        self.assertFalse(should_send_attendance_threshold_alert((0, 0), (0, 4)))

    def test_alerts_when_attendance_falls_below_threshold(self) -> None:
        self.assertTrue(should_send_attendance_threshold_alert((4, 5), (3, 5)))

    def test_does_not_repeat_alert_while_rate_remains_below_threshold(self) -> None:
        self.assertFalse(should_send_attendance_threshold_alert((3, 5), (2, 5)))

    def test_attendance_at_threshold_does_not_alert(self) -> None:
        self.assertFalse(should_send_attendance_threshold_alert((4, 5), (4, 5)))


class AttendanceSummaryTests(unittest.TestCase):
    def test_summary_counts_late_as_attended_and_excludes_excused(self) -> None:
        metadata = MetaData()
        attendance_table = Table(
            "attendance_records",
            metadata,
            Column("id", String, primary_key=True),
            Column("student_id", String, nullable=False),
            Column("status", String, nullable=False),
        )
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async def verify_summary() -> tuple[int, int]:
            async with engine.begin() as connection:
                await connection.run_sync(metadata.create_all)
                await connection.execute(
                    insert(attendance_table),
                    [
                        {"id": "1", "student_id": "student-1", "status": "present"},
                        {"id": "2", "student_id": "student-1", "status": "late"},
                        {"id": "3", "student_id": "student-1", "status": "absent"},
                        {"id": "4", "student_id": "student-1", "status": "excused"},
                    ],
                )
            async with session_factory() as session:
                return await get_attendance_summary(
                    session,
                    attendance_table,
                    "student-1",
                )

        try:
            self.assertEqual(asyncio.run(verify_summary()), (2, 3))
        finally:
            asyncio.run(engine.dispose())


if __name__ == "__main__":
    unittest.main()
