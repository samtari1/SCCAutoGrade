-- Part 1: Simple View
CREATE VIEW simple_view_alice AS
    SELECT student_id, first_name
    FROM students;

SELECT * FROM simple_view_alice;

-- Part 2: View with JOIN
CREATE VIEW join_view_alice AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name, c.course_name, e.grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.enrollment_id
    JOIN courses c ON s.student_id = c.course_id;

SELECT * FROM join_view_alice;

-- Part 3: Aggregate View
CREATE VIEW aggregate_view_alice AS
    SELECT s.major, AVG(e.grade) AS average_grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    GROUP BY s.major;

SELECT * FROM aggregate_view_alice
ORDER BY average_grade DESC;

-- Part 4: Security with Views
CREATE VIEW student_public_info_alice AS
    SELECT CONCAT(first_name, ' ', last_name) AS student_name, major
    FROM students;

GRANT SELECT ON student_public_info_alice TO 'report_user';

-- Part 5: Advanced View
CREATE VIEW top_cs_students_alice AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           s.major,
           AVG(e.grade) AS avg_grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    WHERE s.major = 'Computer Science'
    GROUP BY s.student_id, s.first_name, s.last_name, s.major
    ORDER BY avg_grade DESC
    LIMIT 10;

SELECT * FROM top_cs_students_alice;

-- Part 6: Combined View Usage
CREATE VIEW combined_view_alice AS
    SELECT s.last_name, s.gpa
    FROM students s
    WHERE s.gpa >= 3.5;

SELECT * FROM combined_view_alice
ORDER BY gpa;

-- Part 7: Short Answer Questions
-- 1) Views simplify repeated queries and hide complexity.
-- 2) Views limit what columns/rows users can see.
-- 3) Users can query a stable view even if base tables change.
-- 4) Aggregates summarize data for reporting.
-- 5) Tables store raw data; views are saved queries over data.
