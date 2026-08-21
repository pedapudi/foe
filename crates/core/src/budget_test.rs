use super::Pool;
use crate::Budget;
use foe_log::{BudgetAmount, EventData, ExhaustedLimit, Usage};

fn budget() -> Budget {
    Budget {
        model_calls: 10,
        tokens: Some(1000),
        seconds: None,
        max_depth: 1,
        max_episodes: 3,
        max_concurrent: 1,
        loop_threshold: 3,
    }
}

#[test]
fn model_calls_count_every_request_including_retries() {
    let mut pool = Pool::new(Budget { model_calls: 2, ..budget() });
    assert_eq!(pool.exhausted(), None);
    pool.note_request();
    pool.note_request();
    assert_eq!(pool.exhausted(), Some(ExhaustedLimit::ModelCalls));
}

#[test]
fn tokens_sum_input_and_output_across_requests() {
    let mut pool = Pool::new(budget());
    pool.note_usage(Usage { input: 600, output: 300, cache_read: 500 });
    assert_eq!(pool.remaining().tokens, Some(100));
    pool.note_usage(Usage { input: 90, output: 10, cache_read: 0 });
    assert_eq!(pool.exhausted(), Some(ExhaustedLimit::Tokens));
}

#[test]
fn seconds_elapse_on_the_wall_clock() {
    let pool = Pool::new(Budget { seconds: Some(1), ..budget() });
    assert!(pool.deadline().is_some());
    assert_eq!(pool.exhausted(), None);
    let pool = Pool::new(Budget { seconds: Some(0), ..budget() });
    assert_eq!(pool.exhausted(), Some(ExhaustedLimit::Seconds));
}

#[test]
fn reservation_debits_the_remainder_until_release() {
    let mut pool = Pool::new(budget());
    pool.note_request();
    let granted = pool.reserve("child", BudgetAmount { model_calls: Some(4), tokens: None, seconds: None }).unwrap();
    assert_eq!(
        granted,
        BudgetAmount { model_calls: Some(4), tokens: Some(1000), seconds: None },
        "an unset dimension receives the remainder"
    );
    assert_eq!(pool.remaining().model_calls, Some(5));
    assert_eq!(pool.remaining().tokens, Some(0));
    pool.release("child", BudgetAmount { model_calls: Some(1), tokens: Some(200), seconds: None });
    assert_eq!(pool.remaining().model_calls, Some(8));
    assert_eq!(pool.remaining().tokens, Some(800));
}

#[test]
fn reservation_beyond_the_remainder_names_the_limit() {
    let mut pool = Pool::new(budget());
    let err = pool.reserve("child", BudgetAmount { model_calls: Some(11), tokens: None, seconds: None }).unwrap_err();
    assert_eq!(err, ExhaustedLimit::ModelCalls);
    let err =
        pool.reserve("child", BudgetAmount { model_calls: Some(1), tokens: Some(5000), seconds: None }).unwrap_err();
    assert_eq!(err, ExhaustedLimit::Tokens);
}

#[test]
fn structural_caps_refuse_a_spawn() {
    let mut pool = Pool::new(Budget { max_depth: 0, ..budget() });
    assert_eq!(pool.reserve("a", BudgetAmount::default()).unwrap_err(), ExhaustedLimit::Depth);

    let mut pool = Pool::new(budget());
    pool.reserve("a", BudgetAmount { model_calls: Some(1), ..Default::default() }).unwrap();
    assert_eq!(pool.reserve("b", BudgetAmount::default()).unwrap_err(), ExhaustedLimit::Concurrency);
    pool.release("a", BudgetAmount::default());
    pool.reserve("b", BudgetAmount { model_calls: Some(1), ..Default::default() }).unwrap();
    pool.release("b", BudgetAmount::default());
    assert_eq!(
        pool.reserve("c", BudgetAmount::default()).unwrap_err(),
        ExhaustedLimit::Episodes,
        "max_episodes counts this episode"
    );
}

#[test]
fn folding_reserve_and_release_events_matches_live_calls() {
    let mut live = Pool::new(budget());
    live.note_request();
    let granted = live.reserve("k", BudgetAmount { model_calls: Some(3), ..Default::default() }).unwrap();
    live.release("k", BudgetAmount { model_calls: Some(2), tokens: Some(10), seconds: None });

    let mut folded = Pool::new(budget());
    folded.apply(&EventData::ModelRequest(foe_log::ModelRequest {
        step: 1,
        attempt: 1,
        request_id: "r".into(),
        header_seq: 0,
        consumed: vec![],
        messages: vec![],
    }));
    folded.apply(&EventData::BudgetReserve { child_id: "k".into(), reserved: granted });
    folded.apply(&EventData::BudgetRelease {
        child_id: "k".into(),
        spent: BudgetAmount { model_calls: Some(2), tokens: Some(10), seconds: None },
    });
    assert_eq!(folded.remaining(), live.remaining());
    assert_eq!(folded.active_children(), 0);
}
