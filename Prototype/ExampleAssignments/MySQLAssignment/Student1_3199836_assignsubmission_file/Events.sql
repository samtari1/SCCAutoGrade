-- Part 0: Setup *DONE
CREATE DATABASE demo_logs;
USE demo_logs;

CREATE TABLE logs (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    message VARCHAR(255) NOT NULL,
    log_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE event_demo (
    event_timestamp TIMESTAMP
);

INSERT INTO logs (message, log_timestamp) VALUES
('System started', '2026-02-26 11:19:12'),
('User logged in', '2026-02-26 11:19:12'),
('30 day old test log', '2026-01-25 11:19:12'),	-- older than 30 days
('30 day old test log', '2026-01-25 11:19:12');	-- older than 30 days

-- Question: How many rows did you insert?
-- Answer: I put four rows.
-- Question: What is the current timestamp in your test rows?
-- Answer: The timestamp that I used is '2026-02-26 11:19:12', and '2026-01-25 11:19:12'.
-- ---------------------------------------------------------------------------------------
-- Part 1: One-Time Event (AT) *DONE
DELIMITER //

CREATE EVENT delete_old_logs_once
ON SCHEDULE AT CURRENT_TIMESTAMP + INTERVAL 1 MINUTE
DO
    DELETE FROM logs
    WHERE log_timestamp < NOW() - INTERVAL 30 DAY; 	-- older than 30 days
// 

DELIMITER ;

SHOW EVENTS;

SELECT * FROM logs; 	-- check logs table 

-- Question: Did the old logs get deleted as expected?
-- Answer: Yes, the older log got deleted as expected.
-- Question: What happens if the event is scheduled in the past?
-- Answer: The event will not run.
-- ---------------------------------------------------------------------------------------
-- Part 2: Recurring Event (EVERY) *DONE
DELIMITER //

CREATE EVENT log_event_timestamp_every_minute
ON SCHEDULE EVERY 1 MINUTE
DO
    INSERT INTO event_demo (event_timestamp)
    VALUES (NOW());
//

DELIMITER ;

SHOW EVENTS;

SET GLOBAL event_scheduler = ON;

SELECT * FROM event_demo;

-- Question: How many rows were inserted after 3 minutes?
-- Answer: Three rows were inserted after three minutes.
-- Question: Why do you need to enable the event scheduler?
-- Answer: Event scheduler needs to be on so events will execute.
-- ---------------------------------------------------------------------------------------
-- Part 3: Recurring Event with STARTS and ENDS *DONE
DELIMITER //

CREATE EVENT log_event_timestamp_limited
ON SCHEDULE
    EVERY 1 MINUTE
    STARTS CURRENT_TIMESTAMP 	-- start to current time
    ENDS CURRENT_TIMESTAMP + INTERVAL 3 MINUTE 	-- ends a few minutes later
DO
    INSERT INTO event_demo (event_timestamp)
    VALUES (NOW());
//

DELIMITER ;

SHOW EVENTS;

SELECT * FROM event_demo;

-- Question: How many rows were inserted during the scheduled window?
-- Answer: During the window, three rows were inserted into event_timestamp.
-- Question: What happened after the end time passed?
-- Answer: After it was scheduled to end it stoped logging timestamps and it was removed from the events.
-- ---------------------------------------------------------------------------------------
-- Part 4: Enable / Disable Event *DONE
ALTER EVENT log_event_timestamp_every_minute
DISABLE;

SHOW EVENTS;

ALTER EVENT log_event_timestamp_every_minute
ENABLE;

SELECT * FROM event_demo;

-- Question: What happened while the event was disabled?
-- Answer: While the event was disabled it showed its status as disabled and it stoped logging timestamps every minute.
-- Question: What happened after enabling it again?
-- Answer: The staus changed back to enablied and it was back to logging timestamps every minute.
-- ---------------------------------------------------------------------------------------
-- Part 5: Multiple SQL Statements in an Event *DONE
DELIMITER //

CREATE EVENT event_mulitiple
ON SCHEDULE EVERY 1 MINUTE
DO
BEGIN
    DELETE FROM logs
    WHERE log_timestamp < NOW() - INTERVAL 30 DAY;

    INSERT INTO event_demo (event_timestamp)
    VALUES (NOW());
END //

DELIMITER ;

SHOW EVENTS;

SELECT * FROM event_demo; -- check events

SELECT * FROM logs; -- check logs

INSERT INTO logs (message, log_timestamp)
VALUES ('Old test log', NOW() - INTERVAL 30 DAY); -- insert old date

-- Question: Were both statements executed successfully?
-- Answer: Yes, both statement worked.
-- Question: What happens if one statement fails?
-- Answer: The whole event statement will fail.
-- ---------------------------------------------------------------------------------------
-- Part 6: Dropping Events and Cleaning Up *DONE
DROP EVENT IF EXISTS delete_old_logs_once;
DROP EVENT IF EXISTS log_event_timestamp_every_minute;
DROP EVENT IF EXISTS log_event_timestamp_limited;
DROP EVENT IF EXISTS event_mulitiple;

-- Question: Were all events removed successfully?
-- Answer: Yes, all the events that existed were removed.
-- Question: What happens if you try to drop an event that does not exist?
-- Answer: You get the warning "0 row(s) affected, 1 warning(s): 1305 Event does not exist".
-- Question: Why is it important to clean up recurring events after testing?
-- Answer: I believe its important so that the event won't keep going.

DROP DATABASE IF EXISTS demo_logs; -- drop database if needed.