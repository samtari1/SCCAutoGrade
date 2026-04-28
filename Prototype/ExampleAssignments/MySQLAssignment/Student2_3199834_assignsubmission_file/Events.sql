-- Part #0
-- Creating Database
CREATE DATABASE lab_events;
USE lab_events;

CREATE TABLE logs (
log_id INT PRIMARY KEY AUTO_INCREMENT,
message VARCHAR(250),
timestamp DATETIME
);

CREATE TABLE event_demo (
event_timestamp DATETIME
);

INSERT INTO logs (message, timestamp) VALUES
('This is the 1st message.', NOW()),
('This is the 2nd message.', NOW()),
('This is the 3rd message.', NOW());

-- Questions
-- How many rows did you insert? 3
-- What is the current timestamp in your test rows? 2026-03-01 18:24:59

-- Part #1
-- Creating Event
DELIMITER //

CREATE EVENT delete_old_logs_event
ON SCHEDULE AT NOW() + INTERVAL 1 MINUTE
DO
BEGIN
DELETE FROM logs
WHERE timestamp < DATE_SUB(NOW(), INTERVAL 30 DAY);
END //

DELIMITER ;

-- Checking logs table
SELECT * FROM logs;

-- Questions
-- Did the old logs get deleted as expected? No, because there were no dates that met the requirement.
-- What happens if the event is scheduled in the past? It will not be excuted.
-- What I thought would happen: I didn't think anything would be deleted because none were older than 30 days.
-- What happened: Nothing got deleted because none were older than 30 days.

-- Part #2
-- Creating Event
DELIMITER //

CREATE EVENT run_time_event
ON SCHEDULE EVERY 1 MINUTE
DO
BEGIN
INSERT INTO event_demo (event_timestamp)
VALUES (NOW());
END //

DELIMITER ;

-- Testing
SELECT * FROM event_demo;

-- Questions
-- How many rows were inserted after 3 minutes? 4, 1 at start + 1 every minute.
-- Why do you need to enable the event scheduler? Because events only excute when it's turned on.
-- What I thought would happen: There would be 3 rows inserted.
-- What happened: There were 4 rows inserted.

-- Part #3
-- Updating Event
ALTER EVENT run_time_event
ON SCHEDULE EVERY 1 MINUTE 
STARTS NOW()
ENDS NOW() + INTERVAL 3 MINUTE;

-- Testing
SELECT * FROM event_demo;

-- Questions:
-- How many rows were inserted during the scheduled window? 4, 1 at start + 1 every minute.
-- What happened after the end time passed? The event stopped executing.
-- What I thought would happen: It would insert 4 rows in 3 minutes and then stop executing.
-- What happened: It inserted 4 rows in 3 minutes and then stop executing.

-- Part #4
-- Testing
SELECT * FROM event_demo;

-- Disabling Event
ALTER EVENT run_time_event DISABLE;

-- Testing
SELECT * FROM event_demo;

-- Enabling Event
ALTER EVENT run_time_event ENABLE;

-- Testing
SELECT * FROM event_demo;

-- Questions:
-- What happened while the event was disabled?
-- What happened after enabling it again?
-- What I thought would happen: Disabling it would stop it from running and enabling it would start it running.
-- What happened: When disabled it stop running and when enabled it started running.

-- Part #5
-- Creating Event
DELIMITER //

CREATE EVENT multiple_actions_event
ON SCHEDULE EVERY 1 MINUTE 
DO
BEGIN
DELETE FROM event_demo
WHERE event_timestamp < DATE_SUB(NOW(), INTERVAL 5 MINUTE);

INSERT INTO event_demo (event_timestamp)
VALUES (NOW());
END //

DELIMITER ;

-- Testing
SELECT * FROM event_demo;

-- Questions:
-- Were both statements executed successfully? Yes.
-- What happens if one statement fails? It stops execution and does not work.
-- What I thought would happen: Both actions in the event would execute
-- What happened: Both actions in the event were executed sucesfuly

-- Part #6
-- Showing All Events
SHOW EVENTS;

-- Dropping Events
DROP EVENT IF EXISTS delete_old_logs_event;
DROP EVENT IF EXISTS run_time_event;
DROP EVENT IF EXISTS multiple_actions_event;

-- Questions
-- Were all events removed successfully? Kind of. It says the first 2 don't exists.
-- What happens if you try to drop an event that does not exist? If gives you an error.
-- Why is it important to clean up recurring events after testing? To improve performance and to prevent unwanted data changes.
-- What I thought would happen: All 3 events would be dropped.
-- What happened: Only one event was dropped, the other 2 didn't exists.