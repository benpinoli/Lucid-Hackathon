from sqlalchemy import event


def test_neighbors_use_both_endpoint_indexes(example):
    graph, human, (a, *_) = example
    statements = []

    def capture(connection, cursor, statement, parameters, *_rest):
        if "UNION ALL" in statement and not statement.startswith("EXPLAIN"):
            plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
            statements.extend(row[3] for row in plan)

    event.listen(graph.store.engine, "before_cursor_execute", capture)
    try:
        human.neighbors(a)
    finally:
        event.remove(graph.store.engine, "before_cursor_execute", capture)
    assert any("ix_human_edges_b" in plan for plan in statements)
    assert not any("SCAN human_edges" in plan for plan in statements)
