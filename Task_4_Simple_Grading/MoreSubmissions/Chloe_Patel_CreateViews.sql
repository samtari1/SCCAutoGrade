-- Part 1: Simple View
CREATE VIEW simple_view_chloe AS
    SELECT student_id, first_name
    FROM students;

SELECT * FROM simple_view_chloe;

-- Part 2: View with JOIN
CREATE VIEW join_view_chloe AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           c.course_name,
           e.grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    JOIN courses c ON e.course_id = c.course_id;

SELECT * FROM join_view_chloe;

-- Part 3: Aggregate View
CREATE VIEW aggregate_view_chloe AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           AVG(e.grade) AS average_grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    GROUP BY s.student_id, s.first_name, s.last_name;

SELECT * FROM aggregate_view_chloe
ORDER BY average_grade DESC;

-- Part 4: Security with Views
CREATE VIEW student_public_info_chloe AS
    SELECT CONCAT(first_name, ' ', last_name) AS student_name, major
    FROM students;

GRANT SELECT ON students TO 'report_user';

-- Part 5: Advanced View
CREATE VIEW top_cs_students_chloe AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           s.major,
           AVG(e.grade) AS avg_grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    WHERE s.major = 'Computer Science'
    GROUP BY s.student_id, s.first_name, s.last_name, s.major
    ORDER BY avg_grade ASC
    LIMIT 5;

SELECT * FROM top_cs_students_chloe;

-- Part 6: Combined View Usage
CREATE VIEW combined_view_chloe AS
    SELECT last_name, gpa
    FROM students;

SELECT * FROM combined_view_chloe
WHERE gpa > 3.0
ORDER BY last_name DESC;

-- Part 7: Short Answer Questions
-- 1) Views simplify complex SQL.
-- 2) They improve security by exposing limited data.
-- 3) Views hide schema changes from users.
-- 4) Aggregates are useful for summaries.
-- 5) Tables store data; views provide filtered/query-based access.
