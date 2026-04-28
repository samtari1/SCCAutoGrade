-- Part 0: Setup
-- Create a new database and two tables:
-- logs — with columns for a log ID, message, and timestamp
-- event_demo — with a column to store timestamps
-- Insert some initial test data into the logs table.

CREATE DATABASE events_lab;
USE events_lab;

CREATE TABLE logs (
log_id INT PRIMARY KEY ,
message VARCHAR(100),
log_date TIMESTAMP
);

CREATE TABLE event_demo (
run_time TIMESTAMP);

INSERT INTO logs VALUES(1, 'log1', '2026-02-01 12:00:00');
INSERT INTO logs VALUES(2, 'log2', '2026-02-26 11:50:00');
INSERT INTO logs VALUES(3, 'log3', '2026-01-01 12:00:00');

/*
How many rows did you insert?
3
What is the current timestamp in your test rows?
2026-02-01 12:00:00
2026-02-26 11:50:00
2026-01-01 12:00:00
*/ 

-- ===========================================================================================
-- Part 1: One-Time Event (AT)
-- Task 1: Create a one-time event that deletes logs older than 30 days at a future timestamp.
-- Requirements:
-- Name the event appropriately
-- Schedule it using AT at least 1 minute in the future
-- Delete rows based on log_date
-- After the event runs, check the logs table.

-- a few minutes ahead during first creation, will throw a warning if ran now
CREATE EVENT delete_old_logs
ON SCHEDULE AT '2026-02-26 11:30:00' 
DO
	DELETE FROM logs WHERE log_date < NOW() - INTERVAL 30 DAY;

-- dynamic version that can be ran immediately 
DROP EVENT IF EXISTS delete_old_logs;  
CREATE EVENT delete_old_logs
ON SCHEDULE AT CURRENT_TIME + INTERVAL 1 MINUTE
DO
	DELETE FROM logs WHERE log_date < NOW() - INTERVAL 30 DAY;
/*
Did the old logs get deleted as expected?
Yes, row 3 was deleted from the inital data

What happens if the event is scheduled in the past?
A warning occurs saying the event execution time is in the past and the event was immediately dropped. 

Task 1 Observations: 
Running the event didn't delete any data immediately but once it became the time the event was set to,
it did delete a row. It deleted timestamps from over 30 days ago, deleting one row from my inital data behaving
as expected. I did expect an error to be thrown if an event is scheduled in the past however it only
showed a warning. 

*/
-- ===========================================================================================
-- Part 2: Recurring Event (EVERY)
-- Task 2: Create a recurring event that logs the current timestamp into event_demo every minute.
-- Name the event appropriately
-- Schedule it using EVERY 1 MINUTE
-- Insert the current timestamp into the run_time column
-- Test using SELECT to see the inserted timestamps.

CREATE EVENT timestamp_every_min
ON SCHEDULE EVERY 1 MINUTE
DO
    INSERT INTO event_demo VALUES (CURRENT_TIMESTAMP);

SELECT * FROM event_demo;
/*
How many rows were inserted after 3 minutes?
3

Why do you need to enable the event scheduler?
The event schedular specifies the frequency of events. Events that should run regularly
can only be created once and run automatically rather than running it manually every time
it's needed. In order for this behavior, the event schedular needs to be set to ON and
can be done with SET GLOBAL event_scheduler = ON;

Task 2 Observation: 
This behaves as expected to, this event inserts the current timestamp into the table
every minute. This happens continuously and should be stopped after it's not in use 
anymore to avoid excessive entries. 
*/
-- ===========================================================================================
-- Part 3: Recurring Event with STARTS and ENDS
-- Task 3: Modify the recurring event so it only runs between specific start and end times.
-- Set STARTS to the current time
-- Set ENDS to a few minutes later
-- Ensure it still inserts timestamps into event_demo

-- event ran properly during inital creation but times should be modified to test again
CREATE EVENT timestamp_every_min_start_end
ON SCHEDULE EVERY 1 MINUTE
STARTS '2026-02-26 11:46:00'
ENDS '2026-02-26 11:50:00'
DO
   INSERT INTO event_demo VALUES (CURRENT_TIMESTAMP);
   
-- dynamic version that can be ran immediately 
DROP EVENT IF EXISTS timestamp_every_min_start_end;   
CREATE EVENT timestamp_every_min_start_end
ON SCHEDULE EVERY 1 MINUTE
STARTS CURRENT_TIMESTAMP + INTERVAL 1 MINUTE
ENDS CURRENT_TIMESTAMP + INTERVAL 4 MINUTE
DO
   INSERT INTO event_demo VALUES (CURRENT_TIMESTAMP);   

/*
How many rows were inserted during the scheduled window?
4

What happened after the end time passed?
Timestamps stopped being entered

Task 3 Observation:
This event only starts at the specified timestamp and then begins entering the current timestamp every
minute. After the end timestamp is met, rows stop being added, the event stops. This works as I expected,
the same amount of rows are entered as the minutes between the start and end timestamps. 
*/
-- ===========================================================================================
-- Part 4: Enable / Disable Event
-- Task 4: Disable your recurring event temporarily and observe that it stops executing, then re-enable it.
ALTER EVENT timestamp_every_min DISABLE;
ALTER EVENT timestamp_every_min ENABLE;

/*
What happened while the event was disabled?
When the event was disabled, data stopped being inserted into the table. Instead of seeing a new
insert every minute, it stopped. 

What happened after enabling it again?
Enabling it again allowed for inserts to continue every minute. 

Task 4 Observation:
This matches my expectations. After disabling the event, data was not entered after several minutes passed.
When I enabled it, a new timestamp was entered after a minute and each minute after. disabling and enabling
acts like a pause and continue button, which is what I thought it would do. 
*/
-- ===========================================================================================
-- Part 5: Multiple SQL Statements in an Event
-- Task 5: Create an event that does more than one action, e.g., deletes old logs and logs the action into event_demo in the same event.

DELIMITER //
CREATE EVENT delete_and_log
ON SCHEDULE EVERY 1 DAY
DO
BEGIN
    DELETE FROM logs WHERE log_date < NOW() - INTERVAL 30 DAY;
    INSERT INTO event_demo VALUES (NOW());
END //
DELIMITER ;

/*
Were both statements executed successfully?
Yes

What happens if one statement fails?
If the first statement fails then the next one does not execute. 

Task 5 Observation:
This behaves as I expected it to. Both statements execute in order. When the first
statement successfully executes then so does the second. This appears to be an
acceptable way to log every action. 
*/
-- ===========================================================================================
-- Part 6: Dropping Events and Cleaning Up
-- Task 6: Safely drop all events you created to avoid them running indefinitely.

DROP EVENT IF EXISTS delete_old_logs;
DROP EVENT IF EXISTS timestamp_every_min;
DROP EVENT IF EXISTS timestamp_every_min_start_end;
DROP EVENT IF EXISTS delete_and_log;

/*
Were all events removed successfully?
Yes

What happens if you try to drop an event that does not exist?
Dropping an event that doesnt exist throws an error

Why is it important to clean up recurring events after testing?
It's important to clean up recurring events so don't keep executing and 
use up resources that aren't needed. 

Task 6 Observations: 
Dropping event with IF EXISTS drops the event if it is in the database and passes
with a warning if it does not. This prevents errors from being thrown providing
a safe way to drop data, similar to how to drop other objects. Dropping all 
events with IF EXISTS executed as it should and met my expecations. 
*/