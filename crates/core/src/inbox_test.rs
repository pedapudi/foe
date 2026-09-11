use super::{is_duplicate, Inbox};
use foe_log::{ContentBlock, Event, EventData, InboxItem, InboxSource};

fn item(source: InboxSource, id: Option<&str>) -> InboxItem {
    InboxItem {
        source,
        content: vec![ContentBlock::Text { text: "m".into() }],
        from: None,
        message_id: id.map(str::to_string),
        synthetic: false,
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

/// docs/log-format.md "Inbox": a question has one answer. The rule that
/// drops an item whose identity the log already holds settles which one:
/// the teammate's answer when it arrives before the question's deadline,
/// and the default the runtime delivers otherwise.
#[test]
fn a_second_answer_to_one_question_is_a_duplicate() {
    let default = InboxItem { synthetic: true, ..item(InboxSource::Response, Some("tm_1")) };
    let events = vec![event(1, EventData::InboxItem(item(InboxSource::Request, Some("tm_2"))))];
    assert!(!is_duplicate(&events, &default), "no answer to this question has arrived");
    let self_asked = vec![event(1, EventData::InboxItem(item(InboxSource::Request, Some("tm_1"))))];
    assert!(!is_duplicate(&self_asked, &default), "a question and its answer share an identifier and differ by source");
    let answered = vec![event(1, EventData::InboxItem(item(InboxSource::Response, Some("tm_1"))))];
    assert!(is_duplicate(&answered, &default), "the teammate answered before the deadline");
    let defaulted = vec![event(1, EventData::InboxItem(default))];
    assert!(is_duplicate(&defaulted, &item(InboxSource::Response, Some("tm_1"))), "the default already stands");
}
