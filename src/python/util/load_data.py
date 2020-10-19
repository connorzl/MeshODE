import os
import sys
import trimesh
import torch
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from samplers import sample_faces, fps
import numpy as np


def collate(batch):
    i_idxs = []
    j_idxs = []
    V_is = []
    F_is = []
    E_is = []
    V_surf_is = []
    GV_is = []
    GE_is = []
    V_js = []
    F_js = []
    E_js = []
    V_surf_js = []
    GV_js = []
    GE_js = []

    for (i, j, data_i, data_j) in batch:
        i_idxs.append(i)
        j_idxs.append(j)
        (V_i, F_i, E_i, V_surf_i, GV_i, GE_i) = data_i
        (V_j, F_j, E_j, V_surf_j, GV_j, GE_j) = data_j

        V_is.append(V_i)
        F_is.append(F_i)
        E_is.append(E_i)
        V_surf_is.append(V_surf_i)
        GV_is.append(GV_i)
        GE_is.append(GE_i)

        V_js.append(V_j)
        F_js.append(F_j)
        E_js.append(E_j)
        V_surf_js.append(V_surf_j)
        GV_js.append(GV_j)
        GE_js.append(GE_j)

    V_surf_is = torch.stack(V_surf_is, dim=0)
    V_surf_js = torch.stack(V_surf_js, dim=0)
    return [i_idxs, j_idxs, (V_is, F_is, E_is, GV_is, GE_is),
            (V_js, F_js, E_js, GV_js, GE_js), V_surf_is, V_surf_js]


def compute_deformation_pairs(src_idx, n):
    pairs = []
    if src_idx == -1:
        for i in range(n):
            for j in range(i, n):
                pairs.append((i, j))
                pairs.append((j, i))
    else:
        for i in range(n):
            pairs.append((src_idx, i))
    return pairs


def load_mesh(mesh_path, intermediate=10000, final=2048):
    mesh = trimesh.load(mesh_path, process=False)
    V = mesh.vertices.astype(np.float32)
    F = mesh.faces.astype(np.int32)
    E = mesh.edges.astype(np.int32)

    # Surface vertices for PointNet input.
    V_sample, _, _ = sample_faces(V, F, n_samples=intermediate)
    V_sample, _ = fps(V_sample, final)
    V_sample = V_sample.astype(np.float32)
    
    return V, F, E, V_sample


def load_segmentation(mesh_paths, indices):
    files_dir = os.path.dirname(mesh_paths[0])
    part_sizes_all = []

    for i in range(len(mesh_paths)):
        segmentation_file = "segmentation_" + str(indices[i]).zfill(2) + ".txt"
        segmentation_file = os.path.join(files_dir, segmentation_file)
        
        # Count number of labels, assume labels start from 0.
        curr_part_sizes = []
        curr_label = -1
        curr_size = 0
        with open(segmentation_file, "r") as f:
            lines = [line.rstrip("\n").split(",") for line in f]
            for i, line in enumerate(lines):
                label = int(line[1])
                if i == 0: 
                    curr_label = label
                    curr_size = 1
                elif curr_label == label:
                    curr_size += 1
                else:
                    curr_part_sizes.append(curr_size)
                    curr_label = label
                    curr_size = 1
            curr_part_sizes.append(curr_size)
        part_sizes_all.append(curr_part_sizes)        

    return part_sizes_all


def load_neural_deform_data(mesh_path, intermediate=10000, final=2048):
    V, F, E, V_surf = load_mesh(mesh_path, intermediate, final)
    V = torch.from_numpy(V)
    F = torch.from_numpy(F)
    E = torch.from_numpy(E)
    V_surf = torch.from_numpy(V_surf)

    # Graph vertices and edges for deformation.
    GV = V.clone()
    GE = E.clone()

    return (V, F, E, V_surf, GV, GE)
"""
def load_neural_deform_data(mesh_paths, device, intermediate=10000, final=2048):
    # Load meshes.
    V_all = []
    V_surf_all = []
    F_all = []
    E_all = []
    GV_all = []
    GE_all = []
    for mesh_path in mesh_paths:
        V, F, E, V_surf = load_mesh(mesh_path, intermediate, final)
        V = torch.from_numpy(V)
        F = torch.from_numpy(F)
        E = torch.from_numpy(E)
        V_surf = torch.from_numpy(V_surf)

        V_all.append(V)
        F_all.append(F)
        E_all.append(E)
        V_surf_all.append(V_surf)
        
        # Graph vertices and edges for deformation.
        GV_all.append(V_all[-1].clone())
        GE_all.append(E_all[-1].clone())

    return (V_all, F_all, E_all, V_surf_all), (GV_all, GE_all)
"""
def load_neural_deform_seg_data(mesh_paths, device, intermediate=10000, final=2048):
    V_parts_all = []
    V_parts_combined_all = []
    V_surf_all = []
    F_all = []
    E_all = []
    GV_parts_combined_all = []
    GE_all = []
    GV_origin_all = []
    GV_parts_device_all = []
    for i, mesh_path in enumerate(mesh_paths): 
        V, F, E, V_surf = load_mesh(mesh_path)
        V_parts_combined, V_parts = load_segmentation(mesh_path, i, V)
        
        for j in range(len(V_parts)):
            V_parts[j] = torch.from_numpy(V_parts[j])
        V_parts_all.append(V_parts)
        V_parts_combined_all.append(torch.from_numpy(V_parts_combined))
        F_all.append(torch.from_numpy(F))
        E_all.append(torch.from_numpy(E))
        V_surf_all.append(torch.from_numpy(V_surf))
        
        # Graph vertices and edges for computing forward fitting loss.
        GV_parts_combined_all.append(V_parts_combined_all[-1].clone())
        GE_all.append(E_all[-1].clone())

        # Need to return a mapping from each index of GV_parts_combined_all to label.
        """
        # Graph vertices origins for computing backward fitting loss.
        GV_origin_all.append(GV_parts_combined_all[-1].clone())

        # Graph vertices on device for deformation.
        GV_parts_device = []
        for j in range(len(V_parts)):
            GV_parts_device.append(V_parts[j].to(device))
        GV_parts_device_all.append(GV_parts_device)
        """
    return (V_parts_all, V_parts_combined_all, F_all, E_all, V_surf_all), \
            (GV_parts_combined_all, GE_all)
