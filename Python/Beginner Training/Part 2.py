#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Apr  2 11:28:49 2026

@author: christian
"""

# creating variables
age = 35
name = "Christian"
height = 1.85
is_student = False
print (age)
print (name)

# math and logic
result = 10+5
print (result)
result = 10-5
print (result)

#if age > 30:
    #print ("adult")
    
# Creating a list
fruits = ["apple", "banana", "cherry", "pear", "plum",  "melon"]
numbers = [1, 2, 3, 4, 5]
mixed = [1, "apple", 3.14, True]

print (fruits[0])
print (fruits[3])
print (fruits[-3])

# Modifying lists
fruits.append("orange")

fruits[1] = "blueberry"
print (fruits)

#creating dictionary
person = {
    "name": "Christian",
    "age": 35,
    "city": "Grunzel"
}

# Repeat 5 times
for i in range(8):
    print(f"Person: {i}")
    
    for fruit in fruits:
        print(f"I like {fruit}")
Person = ["Christian", "Anne", "Michel", "Leni", "Oma"]
        
for i in range(5):
    print(f"{Person [i]}")
    print(f"I like {fruits[i]}")
    print ("---")

count = 0
while count < 3:
    print(f"{fruit}")
    count = count + 1
    
count = 0
while count < 3:
        print(f"count: {count}")
        count = count + 1