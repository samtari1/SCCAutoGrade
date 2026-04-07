-- Part 1: Simple View
CREATE OR REPLACE VIEW simple_view_emily AS
    SELECT student_id, first_name
    FROM students;

SELECT * FROM simple_view_emily;

-- Part 2: View with JOIN
CREATE OR REPLACE VIEW join_view_emily AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           c.course_name,
           e.grade
    FROM students s
    JOIN enrollments e ON s.last_name = e.student_id
    JOIN courses c ON e.course_id = c.course_id;

SELECT * FROM join_view_emily;

-- Part 3: Aggregate View
CREATE OR REPLACE VIEW aggregate_view_emily AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           SUM(e.grade) AS average_grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    GROUP BY s.student_id, s.first_name, s.last_name;

SELECT * FROM aggregate_view_emily
ORDER BY average_grade DESC;

-- Part 4: Security with Views
CREATE OR REPLACE VIEW student_public_info_emily AS
    SELECT CONCAT(first_name, ' ', last_name) AS student_name,
           major
    FROM students;

-- Missing GRANT statement for report_user on purpose

-- Part 5: Advanced View
CREATE OR REPLACE VIEW top_cs_students_emily AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           s.major,
           AVG(e.grade) AS avg_grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    GROUP BY s.student_id, s.first_name, s.last_name, s.major
    ORDER BY avg_grade DESC
    LIMIT 5;

SELECT * FROM top_cs_students_emily;

-- Part 6: Combined View Usage
CREATE OR REPLACE VIEW combined_view_emily AS
    SELECT last_name, gpa
    FROM students
    HAVING gpa > 3.5;

SELECT * FROM combined_view_emily
ORDER BY last_name;

-- Part 7: Short Answer Questions
-- 1) Views can make SQL easier to read and reuse.
-- 2) Views can protect sensitive columns.
-- 3) Views reduce impact of schema changes for users.
-- 4) Aggregates are useful for analytical summaries.
-- 5) Table queries are direct; view queries are abstracted.
