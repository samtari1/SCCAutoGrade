USE lab_events;

-- Part 0 Creates and inserts data into a table
-- CREATE TABLE logs(
-- logID INT Auto_increment PRIMARY KEY,
-- message VARCHAR(50),
-- time_stamp DATETIME
-- );

-- CREATE TABLE event_demo(
-- run_time DATETIME
-- );


-- INSERT INTO logs (logID, message, time_stamp)
-- VALUES (1, 'Hi', '2026-1-7 12:00:00');

-- INSERT INTO logs (logID, message, time_stamp)
-- VALUES (2, 'Hello World', '2026-2-15 12:30:00');

-- INSERT INTO logs (logID, message, time_stamp)
-- VALUES (3, 'How are you?', '2026-2-26 01:00:00');

-- Questions
-- 3 rows
-- January 7 2026 at 12:00pm, Feburary 15 2026 at 12:300m and Febuary 23 2026 at 1:30am.


-- Part 1 creates a one time event that deletes records that are over 30 days past the due date one minute into the future
-- CREATE EVENT at_event
-- ON SCHEDULE AT '2026-2-26 11:42:00'
-- DO
-- DELETE FROM logs WHERE log_date < NOW() - INTERVAL 30 DAY;

-- Questions
-- Yes
-- It would give out an error since the time stuff will only work if it is in the future.

-- Part 2 uses a EVERY event that adds a new timestamp every minute.
-- CREATE EVENT every_event
-- ON SCHEDULE EVERY 1 MINUTE
-- DO
-- INSERT INTO event_demo (run_time) VALUES (NOW());

-- Questions
-- 3 rows will show after 3 minutes
-- Enabling the event scheduler helps events functio correctly


-- Part 3 uses an every event but with a start and end.
-- CREATE EVENT everystartend_event
-- ON SCHEDULE EVERY 1 MINUTE
-- STARTS NOW()
-- ENDS '2026-2-26 12:00:00'
-- DO
-- INSERT INTO event_demo (run_time) VALUES (NOW());

-- Questions
-- 3 rows wer shown
-- the event stopped

-- Part 4 Disables the event scheduler and renables it again.
-- SET GLOBAL event_scheduler = off;
-- SET global event_scheduler = on;

-- Questions
-- The event stopped
-- The event picked up where it left off

-- Part 5 uses multiple SQL statements in a event.


-- DELIMITER //
-- CREATE EVENT multipleSQL_event
-- ON SCHEDULE EVERY 1 minute
-- DO
-- BEGIN
-- DELETE FROM logs WHERE log_date < NOW() - INTERVAL 30 DAY;
-- INSERT INTO event_demo (run_time) VALUES (NOW());
-- END//

-- DELIMITER ;

-- Questions
-- Yes
-- If one statement fails then the entire event fails.

-- Part 6 Drops all events to avoid events running indefinitley

-- DROP EVENT multipleSQL_event;
-- DROP EVENT every_event;

-- Yes
-- An error will state that there is an unknown event
-- So they dont keep running on an on and glitch the server