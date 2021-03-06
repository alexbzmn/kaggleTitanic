import fancyimpute.mice as fancyimpute
import numpy as np
import pandas as pd
import os

from sklearn.cross_validation import train_test_split, cross_val_score
from sklearn.ensemble import RandomForestClassifier, AdaBoostClassifier, VotingClassifier, GradientBoostingClassifier
from sklearn.grid_search import GridSearchCV
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing.imputation import Imputer

from titanic_classifier import SurvivalClassifier

import xgboost as xgb


def ingest_data():
    """Read in, combine the training and test data.
    """
    train = pd.read_csv('data/train.csv').assign(Train=1)
    test = (pd.read_csv('data/test.csv').assign(Train=0)
            .assign(Survived=-999)[list(train)])
    return pd.concat([train, test])


extract_lastname = lambda x: x.split(',')[0]


def extract_title(x):
    """Get the person's title from their name. Combine reduntant or less common
    titles together.
    """
    title = x.split(',')[1].split('.')[0][1:]
    if title in ['Mlle', 'Ms']:
        title = 'Miss'
    elif title == 'Mme':
        title = 'Mrs'
    elif title in ['Rev', 'Dr', 'Major', 'Col', 'Capt', 'Jonkheer', 'Dona']:
        title = 'Esteemed'
    elif title in ['Don', 'Lady', 'Sir', 'the Countess']:
        title = 'Royalty'
    return title


first_letter = np.vectorize(lambda x: x[:1])


def ticket_counts(data):
    """Tickets in cases where 2 or more people shared a single ticket.
    """
    ticket_to_count = dict(data.Ticket.value_counts())
    data['TicketCount'] = data['Ticket'].map(ticket_to_count.get)
    data['Ticket'] = np.where(data['TicketCount'] > 1, data['Ticket'], np.nan)
    return data.drop(['TicketCount'], axis=1)


def create_alone_right_place_feature(data):
    data = data.assign(
        AloneRightPlace=np.where((data['FamSize'] == 1) & (data['Sex'] == 0) & (data['Pclass_3'] == 1), 10, 0))
    data['AloneRightPlace'] = data['AloneRightPlace'] + np.where(
        (data['FamSize'] > 1) & (data['Sex'] == 1) & (data['Pclass_2'] == 1), 10, 0)

    return data


def create_dummy_nans(data, col_name):
    """Create dummies for a column in a DataFrame, and preserve np.nans in their
    original places instead of in a separate _nan column.
    """
    deck_cols = [col for col in list(data) if col_name in col]
    for deck_col in deck_cols:
        data[deck_col] = np.where(
            data[col_name + 'nan'] == 1.0, np.nan, data[deck_col])
    return data.drop([col_name + 'nan'], axis=1)


def impute(data):
    """Impute missing values in the Age, Deck, Embarked, and Fare features.
    """

    impute_missing = data.drop(['Survived', 'Train'], axis=1)
    impute_missing_cols = list(impute_missing)

    filled_soft = fancyimpute.MICE().complete(np.array(impute_missing))
    # filled_soft = Imputer().fit_transform(impute_missing)

    results = pd.DataFrame(filled_soft, columns=impute_missing_cols)
    results['Train'] = list(data['Train'])
    results['Survived'] = list(data['Survived'])
    assert results.isnull().sum().sum() == 0, 'Not all NAs removed'

    return results


def feature_engineering(data):
    data = (data
            # Create last name, title, family size, and family features
            .assign(LastName=lambda x: x.Name.map(extract_lastname))
            .assign(Title=lambda x: x.Name.map(extract_title))
            .assign(FamSize=lambda x: x.SibSp + x.Parch + 1)
            .assign(Family=lambda x: [a + '_' + str(b) for a, b in zip(
        list(x.LastName), list(x.FamSize))])
            # Turn the Cabin feature into a Deck feature (A-G)
            .assign(Deck=lambda x: np.where(
        pd.notnull(x.Cabin), first_letter(x.Cabin.fillna('z')), x.Cabin))
            .assign(Deck=lambda x: np.where(x.Deck == 'T', np.nan, x.Deck))

            # Turn Sex into a dummy variable
            .assign(Sex=lambda x: np.where(x.Sex == 'male', 1, 0)))

    return (data

            # Create ticket counts for passengers sharing tickets
            .pipe(ticket_counts)

            # Create dummy variables for the categorical features
            .assign(Pclass=lambda x: x.Pclass.astype(str))
            .pipe(pd.get_dummies, columns=['Pclass', 'Family', 'Title', 'Ticket'])
            .pipe(pd.get_dummies, columns=['Deck'], dummy_na=True)
            .pipe(pd.get_dummies, columns=['Embarked'], dummy_na=True)
            .pipe(create_dummy_nans, 'Deck_')
            .pipe(create_dummy_nans, 'Embarked_')

            # Drop columns we don't need
            .drop(['Name', 'Cabin', 'PassengerId', 'SibSp', 'Parch', 'LastName'],
                  axis=1)

            # Impute NAs using MICE
            .pipe(impute)
            )


def split_data(data):
    """
    Split the combined training/prediction data into separate training and
    prediction sets.
    """

    outcomes = np.array(data.query('Train == 1')['Survived'])
    train = (data.query('Train == 1')
             .drop(['Train', 'Survived'], axis=1))
    to_predict = (data.query('Train == 0')
                  .drop(['Train', 'Survived'], axis=1))

    return train, outcomes, to_predict


def train_test_model(model, model_name, hyperparameters, X_train, X_test, y_train, y_test,
                     folds=5):
    """
    Given a [model] and a set of possible [hyperparameters], along with
    matricies corresponding to hold-out cross-validation, returns a model w/
    optimized hyperparameters, and prints out model evaluation metrics.
    """

    print('Optimizing {0}'.format(model_name))

    optimized_model = GridSearchCV(model, hyperparameters, cv=folds,
                                   n_jobs=-1, verbose=3)
    optimized_model.fit(X_train, y_train)
    optimized_model.predict(X_test)

    print('Optimized parameters:', optimized_model.best_params_)
    print('Model accuracy (hold-out):', optimized_model.score(X_test, y_test))

    kfold_score = np.mean(cross_val_score(
        optimized_model.best_estimator_,
        np.append(X_train, X_test, axis=0),
        np.append(y_train, y_test), cv=folds, n_jobs=-1))

    print('{1} accuracy ({0}-fold):'.format(str(folds), model_name), kfold_score)
    return optimized_model


def majority_vote_ensemble(name, models_votes, train, outcomes, to_predict):
    """Creates a submission from a majority voting ensemble, given training/
    testing data and a list of models and votes.
    """

    model_results = []
    for model, votes in models_votes:
        model.fit(np.array(train), outcomes)
        probs = model.predict(np.array(to_predict))
        probs[probs == 0] = -1
        model_results.append((probs, votes))

    ensemble = pd.read_csv('data/test.csv')[['PassengerId']].assign(
        Survived=0)

    for probs, votes in model_results:
        for i in range(0, votes):
            ensemble = ensemble.assign(Survived=lambda x: x.Survived + probs)

    (ensemble.assign(Survived=lambda x: np.where(x.Survived > 0, 1, 0))
     .to_csv(name, index=False))

    return None


def vote_classifier(output_file_name, models, train, y, test, weights=None):
    voting_classifier = VotingClassifier(estimators=models, voting='hard')
    voting_classifier = voting_classifier.fit(train, y)

    ensemble = pd.read_csv('data/test.csv')[['PassengerId']].assign(
        Survived=voting_classifier.predict(test))

    ensemble.to_csv(output_file_name, index=False)
    return None


def model_and_submit(train, outcomes, to_predict, output_file_name, find_hyperparameters):
    """
    Use a random forest classifier to predict which passengers survive the
    sinking of the Titanic and create a submission.
    """

    if find_hyperparameters:
        X_train, X_test, y_train, y_test = train_test_split(
            train, outcomes, test_size=0.2, random_state=50)

        # gbt_model = train_test_model(
        #     xgb.XGBClassifier(learning_rate=0.05, n_estimators=200,
        #                       seed=25), {
        #         'max_depth': [3, 10, 2],
        #         'min_child_weight': [1, 6, 2],
        #         'gamma': [i / 10.0 for i in range(0, 3)],
        #         'reg_alpha': [0.001, 0.01, 0.1, 1]},
        #     np.array(X_train), np.array(X_test), y_train,
        #     y_test).best_estimator_

        rf_model = train_test_model(
            RandomForestClassifier(n_estimators=800, random_state=25),
            'RandomForestClassifier', {
                'min_samples_split': [1, 3, 10],
                'min_samples_leaf': [1, 3, 10],
                'max_depth': [3, None]},
            X_train, X_test, y_train, y_test).best_estimator_

        lr_model = train_test_model(
            LogisticRegression(random_state=25),
            'LogisticRegression', {
                'C': [0.001, 0.01, 0.1, 1, 10, 100, 1000],
                'class_weight': [None, 'balanced']},
            X_train, X_test, y_train, y_test).best_estimator_

        svm_model = train_test_model(
            SVC(probability=True, random_state=25),
            'SVC', {
                'C': [0.001, 0.01, 0.1, 1, 10, 100, 1000],
                'gamma': np.logspace(-9, 3, 13)},
            X_train, X_test, y_train, y_test).best_estimator_

    else:
        rf_model = RandomForestClassifier(n_estimators=800, random_state=25,
                                          min_samples_split=3, max_depth=None, min_samples_leaf=1)

        # gbt_model = xgb.XGBClassifier(learning_rate=0.05, n_estimators=200,
        #                               seed=25, reg_alpha=0.01, max_depth=3, gamma=0.1,
        #                               min_child_weight=1)

        lr_model = LogisticRegression(random_state=25, C=10,
                                      class_weight='balanced')

        svm_model = SVC(probability=True, random_state=25, C=1000,
                        gamma=0.0001)

    # models_votes = [(rf_model, 2), (lr_model, 1), (svm_model, 1)]
    # majority_vote_ensemble(output_file_name, models_votes, train, outcomes, to_predict)

    vote_classifier(output_file_name, [('rf', rf_model), ('lr', lr_model), ('svm', svm_model)], train, outcomes,
                    to_predict, [2, 1, 1])
    return None


def main():
    data = ingest_data()
    data = feature_engineering(data)

    print(data)
    train, outcomes, to_predict = split_data(data)
    model_and_submit(train, outcomes, to_predict, output_file_name='Output_titanic',
                     find_hyperparameters=False)


def pre_process_data(train_x_file_name, train_y_file_name, test_x_file_name):
    data = ingest_data()
    data = feature_engineering(data)

    print(data)

    train_x, train_y, test_x = split_data(data)

    train_x.to_csv(train_x_file_name, index=False)
    pd.DataFrame(train_y).to_csv(train_y_file_name, index=False)
    test_x.to_csv(test_x_file_name, index=False)


def custom_classifier():
    temp_train_x = 'temp/train_x'
    temp_train_y = 'temp/train_y'
    temp_test_x = 'temp/test_x'

    if not (os.path.isfile(temp_train_x) & os.path.isfile(temp_train_y) & os.path.isfile(temp_test_x)):
        pre_process_data(temp_train_x, temp_train_y, temp_test_x)

    train_x = pd.read_csv(temp_train_x)
    train_y = pd.read_csv(temp_train_y).T.values[0]
    test_x = pd.read_csv(temp_test_x)

    titanic_classifier = SurvivalClassifier(train_x, train_y, test_x, verbose=3)

    titanic_classifier.append_model(
        LogisticRegression(random_state=33, n_jobs=1, C=1000, class_weight=None, max_iter=100, solver='liblinear'),
        'Logistic Regression',
        {
            'C': [0.001, 0.01, 0.1, 1, 10, 100, 1000, 1200],
            'class_weight': [None, 'balanced'],
            'solver': ['newton-cg', 'lbfgs', 'liblinear', 'sag'],
            'max_iter': [50, 100, 300, 400, 500]
        }
    )

    titanic_classifier.append_model(
        RandomForestClassifier(random_state=75, n_jobs=-1, max_depth=None, criterion='gini', min_samples_leaf=1,
                               min_samples_split=3, n_estimators=800, class_weight=None),
        'Random Forest Classifier',
        {
            'n_estimators': [600, 800, 1200],
            'min_samples_split': [1, 3, 10, 50],
            'min_samples_leaf': [1, 3, 10, 50],
            'max_depth': [3, None],
            'criterion': ['gini', 'entropy'],
            'class_weight': ['balanced', None]
        }
    )

    titanic_classifier.append_model(
        RandomForestClassifier(random_state=50, n_jobs=-1, max_depth=None, criterion='entropy', min_samples_leaf=1,
                               min_samples_split=3, n_estimators=800, class_weight=None),
        'Random Forest Classifier Entropy',
        {
            'n_estimators': [600, 800, 1200],
            'min_samples_split': [1, 3, 10, 50],
            'min_samples_leaf': [1, 3, 10, 50],
            'max_depth': [3, None],
            'criterion': ['gini', 'entropy'],
            'class_weight': ['balanced', None]
        }
    )

    titanic_classifier.append_model(
        xgb.XGBClassifier(gamma=0.0, learning_rate=0.01, n_estimators=200,
                          reg_alpha=0.001, subsample=1, max_depth=6,
                          min_child_weight=1, seed=25),
        'Extreme Gradient Boost Classifier',
        {
            'learning_rate': [0.01, 0.05, 0.1, 0.3],
            'max_depth': [3, 6, 10],
            'min_child_weight': [1, 6, 2],
            'gamma': [i / 10.0 for i in range(0, 3)],
            'reg_alpha': [0.001, 0.01, 0.1, 1],
            'subsample': [0.5, 1]
        }
    )

    # titanic_classifier.append_model(
    #     MLPClassifier(random_state=77, hidden_layer_sizes=500, max_iter=1000,
    #                   alpha=0.01, activation='tanh',
    #                   tol=0.01, solver='lbfgs'),
    #     'Multi Layer Perceptron',
    #     {
    #         'hidden_layer_sizes': [(100,), (300,), (500,)],
    #         'activation': ['relu', 'tanh'],
    #         'alpha': [0.0001, 0.003, 0.01],
    #         'max_iter': [200, 400, 600],
    #         'tol': [0.0001, 0.003, 0.01]
    #     }
    # )
    #
    # titanic_classifier.append_model(
    #     SVC(probability=True, random_state=12, C=1000, gamma=0.0001, tol=0.0001),
    #     'SVC',
    #     {
    #         'C': [0.001, 0.01, 0.1, 1, 10, 100, 1000],
    #         'gamma': np.logspace(-9, 3, 13),
    #         'tol': [0.0001, 0.003, 0.01]
    #     }
    # )

    # titanic_classifier.optimize_models()
    # titanic_classifier.plot_learning_curves()
    # titanic_classifier.accuracy_report(folds=3)
    # titanic_classifier.blend_logistic_regression()
    titanic_classifier.voting()
    titanic_classifier.flush_result('voting.csv', 'data/test.csv')

    # titanic_classifier.feature_selection()
    # titanic_classifier.learn_predict_flush('output.csv', 'data/test.csv', weights=[2, 1, 1])


if __name__ == '__main__':
    custom_classifier()
