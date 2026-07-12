from __future__ import annotations

import time

from engines.content.video_ingest_service import VideoIngestService
from storage.bootstrap import create_all
from storage.repositories.content_repository import ContentTaskRepository


def process_one_task() -> bool:
    task_repo = ContentTaskRepository()
    task = task_repo.next_pending()
    if task is None:
        return False
    VideoIngestService().process_task(task.id)
    return True


def main() -> None:
    last_error = None
    for _ in range(30):
        try:
            create_all()
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    if last_error is not None:
        raise last_error
    while True:
        processed = process_one_task()
        if not processed:
            time.sleep(2)


if __name__ == "__main__":
    main()

