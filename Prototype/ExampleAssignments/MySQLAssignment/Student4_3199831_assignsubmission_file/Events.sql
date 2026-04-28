									-- Part 0: Setup
/* Create a new database and two tables:
logs — with columns for a log ID, message, and timestamp
event_demo — with a column to store timestamps
Insert some initial test data into the logs table.
Write SQL comments answering:
1. How many rows did you insert?
- 3

2. What is the current timestamp in your test rows?
- It is the exact time and date when the insert statement was executed 
*/
CREATE database logging_demo;
USE logging_demo;
-- Create logs table
CREATE TABLE logs (
    log_id SERIAL PRIMARY KEY,         -- AUTO_INCREMENT in MySQL
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Create event_demo table
CREATE TABLE event_demo (
    event_time TIMESTAMP
);
-- Insert initial test data into logs
INSERT INTO logs (message) VALUES
('Application started'),
('User logged in'),
('Error: Invalid input');

							-- Part 1: One-Time Event (AT)
/* Task 1: Create a one-time event that deletes logs older than 30 days at a future timestamp.
Requirements:
Name the event appropriately
Schedule it using AT at least 1 minute in the future
Delete rows based on log_date
After the event runs, check the logs table.
Write SQL comments answering:
1. Did the old logs get deleted as expected?
-- Yes, any rows older than 30 days were deleted automatically
 when the event executed.
2. What happens if the event is scheduled in the past?
Execute it immediately (if the time is slightly in the past), OR Reject it with an error because the scheduled time has already passed.
One-time events cannot be created strictly in the past.
*/
 SHOW VARIABLES LIKE 'event_scheduler';
 
CREATE EVENT delete_old_logs_once
ON SCHEDULE AT CURRENT_TIMESTAMP + INTERVAL 1 MINUTE
DO
DELETE FROM logs
WHERE created_at < CURRENT_TIMESTAMP - INTERVAL 30 DAY;

-- After 1+ minute, check table
SELECT * FROM logs;

								-- Part 2: Recurring Event (EVERY)
-- Task 2: Create a recurring event that logs the current timestamp into event_demo every minute.
/* Requirements:
Name the event appropriately
Schedule it using EVERY 1 MINUTE
Insert the current timestamp into the run_time column
Test using SELECT to see the inserted timestamps.
*/
-- Write SQL comments answering:
-- 1. How many rows were inserted after 3 minutes?
-- 3 rows
-- 2. Why do you need to enable the event scheduler?
-- responsible for executing sheduled events. If it is OFF events are created but they will not run 
SHOW VARIABLES LIKE 'event_scheduler';

DROP TABLE IF EXISTS event_demo;
CREATE TABLE event_demo (
    run_time TIMESTAMP
);

CREATE EVENT insert_timestamp_every_minute
ON SCHEDULE EVERY 1 MINUTE
DO
INSERT INTO event_demo (run_time)
VALUES (CURRENT_TIMESTAMP);

-- testing it 
-- SELECT * FROM event_demo;
								-- Part 3: Recurring Event with STARTS and ENDS
-- Task 3: Modify the recurring event so it only runs between specific start and end times.
/* Requirements:

Set STARTS to the current time
Set ENDS to a few minutes later
Ensure it still inserts timestamps into event_demo
Write SQL comments answering:

1. How many rows were inserted during the scheduled window?
-- 3 rows
2. What happened after the end time passed?
-- The event stopped executing no rows were inserted after the ends time 
the event remains defined in the database 
*/
SET GLOBAL event_scheduler = ON;

DROP EVENT IF EXISTS insert_timestamp_every_minute;
CREATE EVENT insert_timestamp_limited
ON SCHEDULE
    EVERY 1 MINUTE
    STARTS CURRENT_TIMESTAMP
    ENDS CURRENT_TIMESTAMP + INTERVAL 3 MINUTE
DO
    INSERT INTO event_demo (run_time)
    VALUES (CURRENT_TIMESTAMP);
    
    -- checking the demo to make sure it works 
    --  SELECT * FROM event_demo; 
    
								-- Part 5: Multiple SQL Statements in an Event
-- Task 5: Create an event that does more than one action, e.g., deletes old logs and logs the action into event_demo in the same event.
/* Requirements:

Use a block with multiple statements

Write SQL comments answering:
1. Were both statements executed successfully?
-- Yes, both the DELETE and INSERT executed in the same event.
        Each time the event runs, old logs are deleted and a timestamp is logged.
2. What happens if one statement fails?
-- If one statement fails, the entire event execution stops for that run.
the statements before the failure still executed 
You would need explicit transactions if you want atomic behavior.
*/

SET GLOBAL event_scheduler = ON;

DELIMITER \\

CREATE EVENT delete_logs_and_record
ON SCHEDULE EVERY 1 MINUTE
DO
BEGIN
    -- Delete logs older than 30 days
    DELETE FROM logs
    WHERE created_at < CURRENT_TIMESTAMP - INTERVAL 30 DAY;

   -- the deletion action into event_demo
    INSERT INTO event_demo (run_time)
    VALUES (CURRENT_TIMESTAMP);
END \\

-- Restore delimiter
DELIMITER ;

SELECT * FROM logs;
SELECT * FROM event_demo;

							-- Part 6: Dropping Events and Cleaning Up
-- Task 6: Safely drop all events you created to avoid them running indefinitely.
-- Drop one-time event from Part 1
DROP EVENT IF EXISTS delete_old_logs_once;

-- Drop recurring event from Part 2
DROP EVENT IF EXISTS insert_timestamp_every_minute;

-- Drop recurring event with STARTS/ENDS from Part 3
DROP EVENT IF EXISTS insert_timestamp_limited;

-- Drop multiple-statements event from Part 5
DROP EVENT IF EXISTS delete_logs_and_record;
-- Write SQL comments answering:

-- 1. Were all events removed successfully?
-- Yes, using DROP EVENT IF EXISTS ensures that all the events we created are safely removed.

-- 2. What happens if you try to drop an event that does not exist?
-- If you use DROP EVENT IF EXISTS, MySQL ignores it. Without IF EXISTS, MySQL will return an error.

-- 3. Why is it important to clean up recurring events after testing?
-- Recurring events continue to run automatically according to their schedule.
-- Leaving them running can:
	-- Fill tables with unwanted data
	-- Consume server resources and few others 