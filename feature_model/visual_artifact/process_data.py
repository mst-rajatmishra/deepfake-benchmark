import os
import argparse
from collections import defaultdict
import dlib
import cv2
from sklearn.externals import joblib
import numpy as np
import pandas as pd

from pipeline import face_utils


def parse_args():
    """Parses input arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', dest='input',default='',
                        help='Path to input image or folder containting multiple images.')
    parser.add_argument('-o', '--output', dest='output', help='Path to save outputs.',
                        default='./output')
    parser.add_argument('-p', '--pipeline', choices=['gan', 'deepfake', 'face2face'], dest='pipeline')
    parser.add_argument('-f', '--features', dest='save_features', action='store_true',
                        help='Set flag to save features, e.g. to fit classifier.',
                        default=False)
    args = parser.parse_args()
    return args


def load_classifiers(pipeline):
    """Loads classifiers for specified pipeline."""
    classifiers = None
    if pipeline == 'gan':
        classifiers = joblib.load('models/gan/bagging_knn.pkl')
    elif pipeline == 'deepfake':
        classifier_mlp = joblib.load('models/deepfake/mlp_df.pkl')
        classifier_logreg = joblib.load('models/deepfake/logreg_df.pkl')
        classifiers = [classifier_mlp, classifier_logreg]
    elif pipeline == 'face2face':
        classifier_mlp = joblib.load('models/face2face/mlp_f2f.pkl')
        classifier_logreg = joblib.load('models/face2face/logreg_f2f.pkl')
        classifiers = [classifier_mlp, classifier_logreg]
    else:
        print ('Unknown pipeline argument.')
        exit(-1)

    return classifiers


def load_facedetector():
    """Loads dlib face and landmark detector."""
    # download if missing http://dlib.net/files/shape_predictor_68_face_landmarks.dat.bz2
    if not os.path.isfile('shape_predictor_68_face_landmarks.dat'):
        print ('Could not find shape_predictor_68_face_landmarks.dat.')
        exit(-1)
    face_detector = dlib.get_frontal_face_detector()
    sp68 = dlib.shape_predictor('shape_predictor_68_face_landmarks.dat')

    return face_detector, sp68


def main(input_path, output_path, pipeline, save_features):
    """Main function to process input files with selected pipeline.

    Given a path to single image or folder and a output path,
    images are processed with selected pipeline.
    Outputs are saved as .csv file. If selected, the computed feature vectors
    for classification are saved as .npy.
    The scores.csv file contains the output score of the different classifiers. The
    'Valid' value indicates if the face detection and segmentation was successful.

    Args:
        input_path: Path to image, or folder containing multiple images.
        output_path: Path to save outputs.
        pipeline: Selected pipeline for processing. Options: 'gan', 'deepfake', 'face2face'
        save_features: Boolean flag. If set true, feature vectors will be saved as single .npy file.
    """
    # transfer image names to list
    if os.path.isdir(input_path):
        file_list = [name for name in os.listdir(input_path) if os.path.isfile(os.path.join(input_path, name))]
    else:
        file_list = [os.path.basename(input_path)]
        input_path = os.path.dirname(input_path)

    if len(file_list) == 0:
        print ('No files at given input path.')
        exit(-1)

    # load classifiers, sanity check
    classifiers = load_classifiers(pipeline)

    # setup face detector and landmark predictor
    face_detector, sp68 = load_facedetector()

    # create save folder
    if not os.path.exists(output_path):
        os.makedirs(output_path)

    result_dict = defaultdict(list)
    feature_vec_list = []
    files_processed = 0

    for input_file in file_list:
        # load image
        img = cv2.imread(os.path.join(input_path, input_file))
        if img is None or img is False:
            print ("Could not open image file: %s" % os.path.join(input_path, input_file))
            continue
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        if pipeline == 'gan':
            # detect and crop faces
            face_crops, final_landmarks = face_utils.get_crops_landmarks(face_detector, sp68, img)
            # call feature extraction, classifier pipeline
            from pipeline import eyecolor
            score_clf, score_HSV, feature_vector, valid_seg = eyecolor.process_faces(classifiers,
                                                                                     face_crops,
                                                                                     final_landmarks,
                                                                                     scale=768)
            # add results to output dict
            result_dict['Filename'].append(input_file)
            result_dict['Score_kNN'].append(score_clf)
            result_dict['Score_HSV'].append(score_HSV)
            result_dict['Valid'].append(int(valid_seg))

            if save_features:
                # add feature vector to list, take care might be None
                if feature_vector is None:
                    feature_vector = np.full(6, np.nan)
                feature_vec_list.append(feature_vector)

        elif pipeline == 'deepfake' or pipeline == 'face2face':
            if pipeline == 'face2face':
                extend_roi = 0.1
            else:
                extend_roi = 0.0
            # detect and crop faces
            face_crops, final_landmarks = face_utils.get_crops_landmarks(face_detector, sp68, img,
                                                                         roi_delta=extend_roi)
            # call feature extraction, classifier pipeline
            from pipeline import texture
            scores, feature_vectors, valid_seg = texture.process_faces(classifiers,
                                                                       face_crops,
                                                                       final_landmarks,
                                                                       pipeline,
                                                                       scale=256)
            # add results to output dict
            result_dict['Filename'].append(input_file)
            result_dict['Score_MLP'].append(scores[0])
            result_dict['Score_LogReg'].append(scores[1])
            result_dict['Valid'].append(int(valid_seg))

            if save_features:
                # add feature vector to list, save invalid as nan
                if feature_vectors[0] is None:
                    feature_vector = np.full(18, np.nan)
                else:
                    feature_vector = feature_vectors[0]
                feature_vec_list.append(feature_vector)

        files_processed += 1
        print ("Files processed: ", files_processed, " of ", len(file_list))

    # save outputs
    result_df = pd.DataFrame.from_dict(result_dict, orient="columns")
    if pipeline == 'gan':
        result_df.to_csv(os.path.join(output_path, "scores.csv"), sep=',',
                         columns=['Filename', 'Score_kNN', 'Score_HSV', 'Valid'], index=False)
    elif pipeline == 'deepfake' or pipeline == 'face2face':
        result_df.to_csv(os.path.join(output_path, "scores.csv"), sep=',',
                         columns=['Filename', 'Score_MLP', 'Score_LogReg', 'Valid'], index=False)

    if save_features:
        feature_vec_list = np.asarray(feature_vec_list, dtype=np.float32)
        np.save(os.path.join(output_path, 'features.npy'), feature_vec_list)


if __name__ == '__main__':
    args_in = parse_args()
    main(args_in.input, args_in.output, args_in.pipeline, args_in.save_features)

