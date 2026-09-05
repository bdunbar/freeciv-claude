Ad-hoc verification scripts written while reverse-engineering the savegame
format. They hardcode scratchpad paths from the session they were written in, so
they need their `W = ...` line updated before they will run again. Kept because
they document exactly what was verified and how:

- `test_orders.py`    move orders execute as commanded
- `test_control.py`   control test: no orders => unit stays put (proves it is
                      our orders moving units, not the AI)
- `test_multiturn.py` order queues persist and drain across turns
- `test_found.py`     founding a city
- `test_prod2.py`     production changes stick across repeated steps
