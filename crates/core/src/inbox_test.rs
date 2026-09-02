use super::{is_duplicate, Inbox};
use foe_log::{ContentBlock, Event, EventData, InboxItem, InboxSource};

fn item(source: InboxSource, id: Option<&str>) -> InboxItem {
    InboxItem {
        source,
        content: vec![ContentBlock::Text { text: "m".into() }],
        from: None,
        message_id: id.map(str::to_string),
    }
}

fn event(seq: u64, data: EventData) -> Event {
    Event { seq, time: 0, version: None, data }
}

#[test]
fn absorb_tracks_items_written_by_others_and_consume_marks_them() {
    let mut inbox = Inbox::default();
    let events = vec![
        event(0, EventData::SeedEnd {}),
        event(1, EventData::InboxItem(item(InboxSource::Task, None))),
        event(2, EventData::InboxItem(item(InboxSource::Parent, None))),
    ];
    inbox.absorb(&events);
    assert_eq!(inbox.pending(), vec![1, 2]);
    inbox.consume(&[1]);
    assert_eq!(inbox.pending(), vec![2]);
    let more = [events.as_slice(), &[event(3, EventData::InboxItem(item(InboxSource::Child, None)))]].concat();
    inbox.absorb(&more);
    assert_eq!(inbox.pending(), vec![2, 3], "a second scan sees only new events");
}

#[test]
fn a_peer_message_with_a_recorded_id_is_a_duplicate() {
    let events = vec![event(1, EventData::InboxItem(item(InboxSource::Peer, Some("tm_1"))))];
    assert!(is_duplicate(&events, &item(InboxSource::Peer, Some("tm_1"))));
    assert!(!is_duplicate(&events, &item(InboxSource::Peer, Some("tm_2"))));
    assert!(!is_duplicate(&events, &item(InboxSource::Parent, Some("tm_1"))), "only peer messages carry delivery ids");
}
