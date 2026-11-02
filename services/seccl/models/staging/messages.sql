/*
interlace:
  materialise: virtual
  strategy: merge
  key: _event_key
  description: Message webhook events (no entity id in the payload; keyed by event)
*/
select * from streams.messages
