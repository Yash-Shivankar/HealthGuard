from django.http import request
from django.shortcuts import render, redirect
import os
import re
import pandas as pd
import numpy as np
from sklearn.preprocessing import LabelEncoder
from sklearn.tree import DecisionTreeClassifier, _tree
from sklearn.model_selection import train_test_split
import csv

training = pd.read_csv('data/Data/Training.csv')

# Extract features and target variable
cols = training.columns[:-1]
X = training[cols]
y = training['prognosis']
print(cols)

# Encode the target variable
le = LabelEncoder()
y = le.fit_transform(y)

# Split the data into training and testing sets
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.33, random_state=42)

# Train a decision tree classifier
clf = DecisionTreeClassifier()
clf.fit(X_train, y_train)

# Load dictionaries and datasets
severityDictionary = {}
description_list = {}
precautionDictionary = {}
remediesDictionary = {}


def calc_condition(symptoms, num_days):
    severity_sum = 0
    for symptom in symptoms:
        severity_sum += severityDictionary.get(symptom, 0)
    if severity_sum / len(symptoms) > 13:
        return "You should take consultation from a doctor."
    else:
        return "It might not be that bad, but you should take precautions."


def getDescription():
    global description_list
    with open('data/MasterData/symptom_Description.csv') as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=',')
        for row in csv_reader:
            description_list[row[0]] = row[1]


def getSeverityDict():
    global severityDictionary
    with open('data/MasterData/symptom_severity.csv') as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=',')
        line_count = 0
        try:
            for row in csv_reader:
                _diction = {row[0]: int(row[1])}  # Convert severity value to integer
                severityDictionary.update(_diction)
        except:
            pass



def getHomeRemedies():
    global remediesDictionary
    with open('data/MasterData/HomeRemedies.csv') as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=',')
        for row in csv_reader:
            remediesDictionary[row[0]] = [row[1], row[2], row[3]]


def getPrecautionDict():
    global precautionDictionary
    with open('data/MasterData/symptom_precaution.csv') as csv_file:
        csv_reader = csv.reader(csv_file, delimiter=',')
        for row in csv_reader:
            precautionDictionary[row[0]] = [row[1], row[2], row[3], row[4]]


def sec_predict(symptoms_exp):
    df = pd.read_csv('data/Data/Training.csv')
    X = df.iloc[:, :-1]
    y = df['prognosis']
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, random_state=20)
    rf_clf = DecisionTreeClassifier()
    rf_clf.fit(X_train, y_train)

    symptoms_dict = {symptom: index for index, symptom in enumerate(X)}
    input_vector = np.zeros(len(symptoms_dict))
    for item in symptoms_exp:
        input_vector[[symptoms_dict[item]]] = 1

    return rf_clf.predict([input_vector])


def tree_to_code(tree, feature_names, symp, days):
    tree_ = tree.tree_

    def recurse(node, depth):
        present_disease = sec_predict(symp)
        condition = calc_condition(symp, days)

        # Retrieve description for the predicted disease
        description = description_list.get(present_disease[0], "")

        # Retrieve precautions for the predicted disease
        precautions = precautionDictionary.get(present_disease[0], [])

        # Retrieve remedies for the predicted disease
        remedies = remediesDictionary.get(present_disease[0], [])

        if not remedies:
            remedies = 'Remedies for this disease were not found'

        result = {
            'disease': present_disease[0],
            'description': description,
            'condition': condition,
            'precautions': precautions,
            'remedies': remedies
        }
        return result

    return recurse(0, 1)


def home(request):
    return render(request, 'home.html')


def register(request):
    return render(request, 'register.html')


def symptoms(request):
    symptoms = cols
    return render(request, 'symptom.html', {'symptoms': symptoms})


def submit_form(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        contact = request.POST.get('contact')
        age = request.POST.get('age')
        height = request.POST.get('height')
        weight = request.POST.get('weight')
        print(name, contact, age, height, weight)

        try:
            # Write data to CSV file
            with open('data.csv', 'a', newline='') as file:
                writer = csv.writer(file)
                writer.writerow([name, contact, age, height, weight])

            # Redirect to home page after form submission
            return redirect('symptoms')

        except Exception as e:
            print(f"An exception occurred: {e}")
            return render(request, 'symptom.html', {'error': 'An error occurred while submitting the form'})

    return render(request, 'symptom.html')




# def home():
#     symptoms = cols
#     return render('index.html', symptoms=symptoms)


def predict(request):
    if request.method == 'POST':
        print("Form data:", request.POST)
        symptoms = request.POST.getlist('symptoms[]')
        days = request.POST.get('days')  # Convert days to integer

        # Load dictionaries and datasets
        getDescription()
        getSeverityDict()
        getPrecautionDict()
        getHomeRemedies()

        # Call the tree_to_code function to make predictions
        result = tree_to_code(clf, cols, symptoms, int(days))
        return render(request, 'result.html', {'result': result})

