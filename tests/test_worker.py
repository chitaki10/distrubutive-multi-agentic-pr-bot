from prbot import worker


async def test_main_wires_db_client_and_worker_together(monkeypatch):
    calls = []

    async def fake_init_db():
        calls.append("init_db")

    class FakeClient:
        pass

    async def fake_connect(target):
        calls.append(("connect", target))
        return FakeClient()

    class FakeWorker:
        def __init__(self, client, *, task_queue, workflows, activities):
            calls.append(("worker_init", task_queue, workflows, activities))
            self.client = client

        async def run(self):
            calls.append("worker_run")

    monkeypatch.setattr(worker, "init_db", fake_init_db)
    monkeypatch.setattr(worker.Client, "connect", staticmethod(fake_connect))
    monkeypatch.setattr(worker, "Worker", FakeWorker)

    await worker.main()

    assert calls[0] == "init_db"
    assert calls[1] == ("connect", "localhost:7233")
    assert calls[2][0] == "worker_init"
    assert calls[2][1] == worker.TASK_QUEUE
    assert calls[2][2] == [worker.PRReviewWorkflow]
    assert calls[2][3] == [
        worker.fetch_diff_activity,
        worker.review_activity,
        worker.post_comment_activity,
        worker.set_review_status_activity,
    ]
    assert calls[3] == "worker_run"
