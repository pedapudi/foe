use super::Pool;
use crate::Budget;
use foe_log::{BudgetAmount, EventData, ExhaustedLimit, Usage};

pub fn budget() -> Budget {
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
    let granted = pool.reserve("child", BudgetAmount { model_calls: Some(4), ..Default::default() }).unwrap();
    assert_eq!(
        granted,
        BudgetAmount {
            model_calls: Some(4),
            tokens: Some(1000),
            episodes: Some(2),
            concurrent: Some(1),
            ..Default::default()
        },
        "an unset dimension receives the remainder"
    );
    assert_eq!(pool.remaining().model_calls, Some(5));
    assert_eq!(pool.remaining().tokens, Some(0));
    pool.release("child", BudgetAmount { model_calls: Some(1), tokens: Some(200), ..Default::default() });
    assert_eq!(pool.remaining().model_calls, Some(8));
    assert_eq!(pool.remaining().tokens, Some(800));
}

#[test]
fn reservation_beyond_the_remainder_names_the_limit() {
    let mut pool = Pool::new(budget());
    let err = pool.reserve("child", BudgetAmount { model_calls: Some(11), ..Default::default() }).unwrap_err();
    assert_eq!(err, ExhaustedLimit::ModelCalls);
    let err = pool
        .reserve("child", BudgetAmount { model_calls: Some(1), tokens: Some(5000), ..Default::default() })
        .unwrap_err();
    assert_eq!(err, ExhaustedLimit::Tokens);
}

#[test]
fn a_grant_of_zero_on_any_dimension_names_that_limit() {
    let mut pool = Pool::new(budget());
    pool.note_usage(Usage { input: 1000, output: 0, cache_read: 0 });
    let err = pool.reserve("child", BudgetAmount { model_calls: Some(1), ..Default::default() }).unwrap_err();
    assert_eq!(err, ExhaustedLimit::Tokens, "the whole token remainder is zero, so no child could start");
    assert_eq!(pool.active_children(), 0, "a refused reservation holds nothing");
}

#[test]
fn structural_caps_refuse_a_spawn() {
    let mut pool = Pool::new(Budget { max_depth: 0, ..budget() });
    assert_eq!(pool.reserve("a", BudgetAmount::default()).unwrap_err(), ExhaustedLimit::Depth);

    let mut pool = Pool::new(budget());
    let one_episode = |calls| BudgetAmount { model_calls: Some(calls), episodes: Some(1), ..Default::default() };
    pool.reserve("a", one_episode(1)).unwrap();
    assert_eq!(pool.reserve("b", BudgetAmount::default()).unwrap_err(), ExhaustedLimit::Concurrency);
    pool.release("a", one_episode(1));
    pool.reserve("b", one_episode(1)).unwrap();
    pool.release("b", one_episode(1));
    assert_eq!(
        pool.reserve("c", BudgetAmount::default()).unwrap_err(),
        ExhaustedLimit::Episodes,
        "max_episodes counts this episode"
    );
}

/// docs/config.md `budget`: `max_episodes` is the lifetime count of
/// episodes in the tree, this one included. The count reaches the pool
/// through the reservation a child receives and the count it reports back.
#[test]
fn the_episode_allowance_is_shared_out_and_reported_back() {
    let mut pool = Pool::new(Budget { max_episodes: 4, max_concurrent: 2, ..budget() });
    let first = pool.reserve("a", BudgetAmount { model_calls: Some(1), ..Default::default() }).unwrap();
    assert_eq!(first.episodes, Some(3), "a request that names no count takes the whole remainder");
    assert_eq!(
        pool.reserve("b", BudgetAmount { model_calls: Some(1), ..Default::default() }).unwrap_err(),
        ExhaustedLimit::Episodes,
        "the first reservation holds the rest of the allowance"
    );

    let mut pool = Pool::new(Budget { max_episodes: 4, max_concurrent: 2, ..budget() });
    pool.reserve("a", BudgetAmount { model_calls: Some(1), episodes: Some(2), ..Default::default() }).unwrap();
    pool.release("a", BudgetAmount { model_calls: Some(1), episodes: Some(2), ..Default::default() });
    assert_eq!(pool.remaining().episodes, Some(1), "a settled subtree of two keeps its share");
    pool.reserve("b", BudgetAmount { model_calls: Some(1), ..Default::default() }).unwrap();
    assert_eq!(pool.reserve("c", BudgetAmount::default()).unwrap_err(), ExhaustedLimit::Episodes);
}

/// docs/config.md `budget`: `max_concurrent` bounds every child episode
/// running below this episode. Each active subtree holds its granted slots.
#[test]
fn concurrent_slots_are_leased_to_active_subtrees() {
    let mut pool = Pool::new(Budget { max_episodes: 6, max_concurrent: 3, ..budget() });
    let subtree = pool
        .reserve(
            "subtree",
            BudgetAmount {
                model_calls: Some(1),
                tokens: Some(1),
                episodes: Some(2),
                concurrent: Some(2),
                ..Default::default()
            },
        )
        .unwrap();
    assert_eq!(subtree.concurrent, Some(2));
    assert_eq!(pool.remaining().concurrent, Some(1));
    let leaf = || BudgetAmount {
        model_calls: Some(1),
        tokens: Some(1),
        episodes: Some(1),
        concurrent: Some(1),
        ..Default::default()
    };
    pool.reserve("leaf", leaf()).unwrap();
    assert_eq!(pool.reserve("blocked", leaf()).unwrap_err(), ExhaustedLimit::Concurrency);
    pool.release("subtree", BudgetAmount::default());
    assert_eq!(pool.remaining().concurrent, Some(2), "settling a subtree returns its concurrent slots");
}

#[test]
fn folding_reserve_and_release_events_matches_live_calls() {
    let mut live = Pool::new(budget());
    live.note_request();
    let granted = live.reserve("k", BudgetAmount { model_calls: Some(3), ..Default::default() }).unwrap();
    live.release("k", BudgetAmount { model_calls: Some(2), tokens: Some(10), ..Default::default() });

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
        spent: BudgetAmount { model_calls: Some(2), tokens: Some(10), ..Default::default() },
    });
    assert_eq!(folded.remaining(), live.remaining());
    assert_eq!(folded.active_children(), 0);
}
