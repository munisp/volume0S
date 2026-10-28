import pytest

from fluvio_sink import FluvioCensusSink


class Output:
    def __init__(self, acknowledged=True):
        self.acknowledged = acknowledged

    async def async_wait(self):
        return {"offset": 1} if self.acknowledged else None


class Producer:
    def __init__(self, acknowledged=True):
        self.acknowledged = acknowledged
        self.records = []
        self.flushed = False

    async def async_send(self, key, value):
        self.records.append((bytes(key), bytes(value)))
        return Output(self.acknowledged)

    async def async_flush(self):
        self.flushed = True


class Client:
    def __init__(self, producer):
        self.producer = producer

    def topic_producer(self, topic):
        assert topic == "census.pings"
        return self.producer


@pytest.mark.asyncio
async def test_keyed_fluvio_publication_waits_for_acknowledgement():
    producer = Producer()
    sink = FluvioCensusSink(Client(producer), "census.pings")
    await sink.publish("census.pings", "tenant-a", '{"event":1}')
    assert producer.records == [(b"tenant-a", b'{"event":1}')]
    assert producer.flushed


@pytest.mark.asyncio
async def test_unacknowledged_or_wrong_topic_publication_fails_closed():
    sink = FluvioCensusSink(Client(Producer(acknowledged=False)), "census.pings")
    with pytest.raises(TimeoutError):
        await sink.publish("census.pings", "tenant-a", "{}")
    with pytest.raises(ValueError):
        await sink.publish("other", "tenant-a", "{}")
