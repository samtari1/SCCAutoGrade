-- Part 1: Simple View
CREATE VIEW simple_view_daniel AS
    SELECT student_id, first_name
    FROM student;

SELECT * FROM simple_view_daniel;

-- Part 2: View with JOIN
CREATE VIEW join_view_daniel AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           c.course_name,
           e.grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    JOIN courses c ON e.course_id = c.course_id;

SELECT * FROM join_view_daniel;

-- Part 3: Aggregate View
CREATE VIEW aggregate_view_daniel AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           AVG(e.grade) AS average_grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id;

SELECT * FROM aggregate_view_daniel
ORDER BY average_grade DESC;

-- Part 4: Security with Views
CREATE VIEW student_public_info_daniel AS
    SELECT CONCAT(first_name, ' ', last_name) AS student_name, major
    FROM students;

GRANT SELECT ON student_public_info_daniel TO 'report_user';

-- Part 5: Advanced View
CREATE VIEW top_cs_students_daniel AS
    SELECT CONCAT(s.first_name, ' ', s.last_name) AS student_name,
           AVG(e.grade) AS avg_grade
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    WHERE s.major = 'Computer Science'
    GROUP BY s.student_id, s.first_name, s.last_name
    ORDER BY avg_grade DESC
    LIMIT 5;

SELECT * FROM top_cs_students_daniel;

-- Part 6: Combined View Usage
CREATE VIEW combined_view_daniel AS
    SELECT CONCAT(first_name, ' ', last_name) AS student_name,
           last_name,
           gpa
    FROM students;

SELECT * FROM combined_view_daniel
WHERE gpa > 3.5
ORDER BY student_name;

-- Part 7: Short Answer Questions
-- 1) Views simplify repeated and complex logic.
-- 2) Views can hide sensitive fields.
-- 3) Views provide a stable interface if tables change.
-- 4) Aggregates summarize data quickly.
-- 5) Direct table queries expose everything; views are curated.
