import numpy as np
import torch.nn as nn
import torch

from deepymod_torch.sparsity import scaling
from torch.utils.tensorboard import SummaryWriter


def deepmod_init(network_config, library_config):
    '''
    Builds a fully-connected NN according to specified parameters.
    '''
    # Building network
    input_dim = network_config['input_dim']
    hidden_dim = network_config['hidden_dim']
    layers = network_config['layers']
    output_dim = network_config['output_dim']

    network = [nn.Linear(input_dim, hidden_dim), nn.Tanh()]  # Input layer

    for hidden_layer in np.arange(layers):  # Hidden layers
        network.append(nn.Linear(hidden_dim, hidden_dim))
        network.append(nn.Tanh())

    network.append(nn.Linear(hidden_dim, output_dim))  # Output layer
    torch_network = nn.Sequential(*network)

    # Building coefficient vectors and sparsity_masks
    library_function = library_config['type']

    sample_data = torch.ones(1, input_dim, requires_grad=True)  # we run a single forward pass to infer shapes
    sample_prediction = torch_network(sample_data)
    _, theta = library_function(sample_data, sample_prediction, library_config)
    total_terms = theta.shape[1]

    coeff_vector_list = [torch.randn((total_terms, 1), dtype=torch.float32, requires_grad=True) for _ in torch.arange(output_dim)]
    sparsity_mask_list = [torch.arange(total_terms) for _ in torch.arange(output_dim)]

    return torch_network, coeff_vector_list, sparsity_mask_list


def train(data, target, network, coeff_vector_list, sparsity_mask_list, library_config, optim_config):
    '''
    Trains deepmod NN.
    '''

    max_iterations = optim_config['max_iterations']
    l1 = optim_config['lambda']
    library_function = library_config['type']

    optimizer = torch.optim.Adam([{'params': network.parameters(), 'lr': 0.002}, {'params': coeff_vector_list, 'lr': 0.002}])

    # preparing tensorboard writer
    writer = SummaryWriter()
    custom_board = {'Costs': {'MSE': ['Multiline', ['MSE_' + str(idx) for idx in np.arange(len(coeff_vector_list))]],
                              'Regression': ['Multiline', ['Regression_' + str(idx) for idx in np.arange(len(coeff_vector_list))]],
                              'L1': ['Multiline', ['L1_' + str(idx) for idx in np.arange(len(coeff_vector_list))]]}, 'Coefficients': {}, 'Scaled coefficients': {}}
    for idx in np.arange(len(coeff_vector_list)):
        custom_board['Coefficients']['Vector_' + str(idx)] = ['Multiline', ['coeff_' + str(idx) + '_' + str(element_idx) for element_idx in np.arange(coeff_vector_list[idx].shape[0])]]
        custom_board['Scaled coefficients']['Vector_' + str(idx)] = ['Multiline', ['scaled_coeff_' + str(idx) + '_' + str(element_idx) for element_idx in np.arange(coeff_vector_list[idx].shape[0])]]

    writer.add_custom_scalars(custom_board)


    # Training
    print('Epoch | Total loss | MSE | PI | L1 ')
    for iteration in np.arange(max_iterations):
        # Calculating prediction and library
        prediction = network(data)
        time_deriv_list, theta = library_function(data, prediction, library_config)
        sparse_theta_list = [theta[:, sparsity_mask] for sparsity_mask in sparsity_mask_list]

        # Scaling
        coeff_vector_scaled_list = [scaling(coeff_vector, sparse_theta, time_deriv) for time_deriv, sparse_theta, coeff_vector in zip(time_deriv_list, sparse_theta_list, coeff_vector_list)]

        # Calculating PI
        reg_cost_list = torch.stack([torch.mean((time_deriv - sparse_theta @ coeff_vector)**2) for time_deriv, sparse_theta, coeff_vector in zip(time_deriv_list, sparse_theta_list, coeff_vector_list)])
        loss_reg = torch.sum(reg_cost_list)

        # Calculating MSE
        MSE_cost_list = torch.mean((prediction - target)**2, dim=0)
        loss_MSE = torch.sum(MSE_cost_list)

        # Calculating L1
        l1_cost_list = torch.stack([torch.sum(torch.abs(coeff_vector_scaled)) for coeff_vector_scaled in coeff_vector_scaled_list])
        loss_l1 = l1 * torch.sum(l1_cost_list)

        # Calculating total loss
        loss = loss_MSE + loss_reg + loss_l1

        # Optimizer step
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # Tensorboard stuff
        if iteration % 50 == 0:
            writer.add_scalar('Total loss', loss, iteration)
            for idx in np.arange(len(MSE_cost_list)):
                # Costs
                writer.add_scalar('MSE '+str(idx), MSE_cost_list[idx], iteration)
                writer.add_scalar('Regression '+str(idx), reg_cost_list[idx], iteration)
                writer.add_scalar('L1 '+str(idx), l1_cost_list[idx], iteration)

                # Coefficients
                for element_idx, element in enumerate(torch.unbind(coeff_vector_list[idx])):
                    writer.add_scalar('coeff ' + str(idx) + ' ' + str(element_idx), element, iteration)

                # Scaled coefficients
                for element_idx, element in enumerate(torch.unbind(coeff_vector_scaled_list[idx])):
                    writer.add_scalar('scaled_coeff ' + str(idx) + ' ' + str(element_idx), element, iteration)


        # Printing
        if iteration % 500 == 0:
            print(iteration, "%.1E" % loss.item(), "%.1E" % loss_MSE.item(), "%.1E" % loss_reg.item(), "%.1E" % loss_l1.item())
            for coeff_vector in zip(coeff_vector_list, coeff_vector_scaled_list):
                print(coeff_vector[0])
    
    writer.close()
    return time_deriv_list, theta, coeff_vector_list
